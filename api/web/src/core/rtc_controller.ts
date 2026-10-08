// Copyright (C) 2026 The Android Open Source Project
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
// http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

import { GrpcWebTransport } from "./transport";
import { InputChannel } from "./input_channel";
import { SensorsChannel } from "./sensors_channel";
import { DeviceError } from "./auth";
import { subscribe, Unsubscribe } from "./streaming";
import { TrackType } from "@android/emulator-services/common";
import {
  JsepMessage,
  RtcService,
  RtcServiceClientImpl,
  SessionDescription_Type,
} from "@android/emulator-services/webrtc_service";

export { TrackType };

export enum RtcConnectionState {
  DISCONNECTED = "disconnected",
  CONNECTING = "connecting",
  CONNECTED = "connected",
  RECONNECTING = "reconnecting",
  FAILED = "failed",
  CLOSED = "closed",
}

export interface RtcControllerOptions {
  transport: GrpcWebTransport;
  inputChannel: InputChannel;
  sensorsChannel?: SensorsChannel;
  peerConnectionConfig?: RTCConfiguration;
  createPeerConnection?: (config?: RTCConfiguration) => RTCPeerConnection;
}

type EventListener = (...args: any[]) => void;

/**
 * Coordinates WebRTC signaling, JSEP offer/answer, and media stream tracks.
 */
export class RtcController {
  private readonly rpc: RtcService;
  private readonly inputChannel: InputChannel;
  private readonly sensorsChannel?: SensorsChannel;
  private readonly pcConfig?: RTCConfiguration;
  private readonly createPcFn: (config?: RTCConfiguration) => RTCPeerConnection;

  private peerConnection: RTCPeerConnection | null = null;
  private sessionGuid: string | null = null;
  private connectionState: RtcConnectionState = RtcConnectionState.DISCONNECTED;
  private listeners: Map<string, Set<EventListener>> = new Map();
  private mediaStream: MediaStream | null = null;
  private streamCancelFn: Unsubscribe | null = null;
  private pendingRemoteCandidates: any[] = [];
  private isRemoteDescriptionSet = false;
  private jsepProcessingChain: Promise<void> = Promise.resolve();

  constructor(options: RtcControllerOptions) {
    this.rpc = new RtcServiceClientImpl(options.transport);
    this.inputChannel = options.inputChannel;
    this.sensorsChannel = options.sensorsChannel;
    this.pcConfig = options.peerConnectionConfig;
    this.createPcFn =
      options.createPeerConnection ||
      ((config) => new (window as any).RTCPeerConnection(config));
  }

  public get state(): RtcConnectionState {
    return this.connectionState;
  }

  public get stream(): MediaStream | null {
    return this.mediaStream;
  }

  /**
   * Subscribes to events ("stateChange", "stream", "error").
   */
  public on(event: string, listener: EventListener) {
    if (!this.listeners.has(event)) {
      this.listeners.set(event, new Set());
    }
    this.listeners.get(event)!.add(listener);
  }

  public off(event: string, listener: EventListener) {
    this.listeners.get(event)?.delete(listener);
  }

  private emit(event: string, ...args: any[]) {
    this.listeners.get(event)?.forEach((fn) => {
      try {
        fn(...args);
      } catch (err) {
        console.error(`Error in ${event} listener:`, err);
      }
    });
  }

  private setState(state: RtcConnectionState) {
    if (this.connectionState !== state) {
      this.connectionState = state;
      this.emit("stateChange", state);
    }
  }

  /**
   * Initiates WebRTC connection handshake with emulator RtcService.
   */
  public async connect(): Promise<void> {
    if (
      this.connectionState === RtcConnectionState.CONNECTING ||
      this.connectionState === RtcConnectionState.CONNECTED
    ) {
      return;
    }
    if (this.peerConnection || this.streamCancelFn) {
      this.disconnect();
    }

    this.pendingRemoteCandidates = [];
    this.isRemoteDescriptionSet = false;
    this.jsepProcessingChain = Promise.resolve();
    this.setState(RtcConnectionState.CONNECTING);

    try {
      // 1. Request WebRTC session and ICE configurations
      const response = await this.rpc.RequestRtcStream({
        requestedTracks: [
          TrackType.TRACK_TYPE_SCREEN_PRIMARY,
          TrackType.TRACK_TYPE_AUDIO_SPEAKER,
        ],
        secondaryDisplayIds: [],
      });

      const guid = response.handle?.sessionId ?? "";
      const iceServers: RTCIceServer[] = (response.iceServers || []).map((s) => ({
        urls: s.urls,
        username: s.username || undefined,
        credential: s.credential || undefined,
      }));
      this.sessionGuid = guid;

      // 2. Initialize RTCPeerConnection
      const rtcConfig: RTCConfiguration = {
        ...this.pcConfig,
        iceServers: iceServers.length > 0 ? iceServers : this.pcConfig?.iceServers,
      };

      this.peerConnection = this.createPcFn(rtcConfig);
      this.setupPeerConnectionEvents(this.peerConnection);

      // 3. Create input_v2 and sensors_v2 DataChannels
      const dataChannel = this.peerConnection.createDataChannel("input_v2", {
        ordered: true,
      });
      this.inputChannel.attachDataChannel(dataChannel);
      if (this.sensorsChannel) {
        const sensorsDataChannel = this.peerConnection.createDataChannel("sensors_v2", {
          ordered: false,
          maxRetransmits: 0,
        });
        this.sensorsChannel.attachDataChannel(sensorsDataChannel);
      }

      // 4. Start listening for incoming JSEP signaling stream
      this.startJsepStream(guid);

      // 5. Create local SDP Offer and inject 127.0.0.1 loopback active TCP candidate
      // so Chrome's Port::CanPairWith() pairs with the device's 127.0.0.1 adb forward passive candidate.
      const offer = await this.peerConnection.createOffer({
        offerToReceiveVideo: true,
        offerToReceiveAudio: true,
      });
      if (offer.sdp) {
        offer.sdp = this.injectLoopbackActiveCandidate(offer.sdp);
      }
      await this.peerConnection.setLocalDescription(offer);

      // 6. Send SDP Offer to emulator
      await this.rpc.SendJsepMessage({
        handle: { sessionId: guid },
        sdp: {
          type: SessionDescription_Type.OFFER,
          sdp: offer.sdp || "",
        },
      });
    } catch (err: any) {
      this.setState(RtcConnectionState.FAILED);
      this.emit("error", new DeviceError(`WebRTC connect failed: ${err.message}`));
      this.disconnect();
      throw err;
    }
  }

  /**
   * Processes an incoming JSEP message (SDP Answer or ICE Candidate) with candidate buffering.
   */
  public async handleJsepMessage(json: any): Promise<void> {
    if (json.type === "answer") {
      if (this.peerConnection) {
        await this.peerConnection.setRemoteDescription(
          typeof RTCSessionDescription !== "undefined" ? new RTCSessionDescription(json) : json
        );
        this.isRemoteDescriptionSet = true;
        const queued = this.pendingRemoteCandidates.splice(0, this.pendingRemoteCandidates.length);
        for (const cand of queued) {
          try {
            await this.peerConnection.addIceCandidate(
              typeof RTCIceCandidate !== "undefined" ? new RTCIceCandidate(cand) : cand
            );
          } catch (err) {
            console.warn("Ignored error adding buffered ICE candidate:", err);
          }
        }
      }
    } else if (json.candidate !== undefined) {
      if (this.peerConnection) {
        if (!this.isRemoteDescriptionSet && !this.peerConnection.remoteDescription) {
          this.pendingRemoteCandidates.push(json);
        } else {
          try {
            await this.peerConnection.addIceCandidate(
              typeof RTCIceCandidate !== "undefined" ? new RTCIceCandidate(json) : json
            );
          } catch (err) {
            console.warn("Ignored error adding ICE candidate:", err);
          }
        }
      }
    }
  }

  public disconnect(): void {
    const activeGuid = this.sessionGuid;
    if (this.streamCancelFn) {
      this.streamCancelFn();
      this.streamCancelFn = null;
    }

    if (activeGuid) {
      this.rpc.CloseRtcStream({ handle: { sessionId: activeGuid } }).catch(() => {});
    }

    if (this.peerConnection) {
      this.peerConnection.close();
      this.peerConnection = null;
    }

    this.mediaStream = null;
    this.sessionGuid = null;
    this.pendingRemoteCandidates = [];
    this.isRemoteDescriptionSet = false;
    this.jsepProcessingChain = Promise.resolve();
    this.setState(RtcConnectionState.DISCONNECTED);
  }

  private setupPeerConnectionEvents(pc: RTCPeerConnection) {
    pc.onicecandidate = (event) => {
      if (this.sessionGuid) {
        const candidate = event.candidate;
        this.rpc
          .SendJsepMessage({
            handle: { sessionId: this.sessionGuid },
            iceCandidate: {
              candidate: candidate?.candidate ?? "",
              sdpMid: candidate?.sdpMid ?? "",
              sdpMLineIndex: candidate?.sdpMLineIndex ?? 0,
              usernameFragment: candidate?.usernameFragment ?? undefined,
            },
          })
          .catch((err) => console.error("Failed to send local ICE candidate:", err));
      }
    };

    pc.ontrack = (event) => {
      const existingTracks = this.mediaStream ? this.mediaStream.getTracks() : [];
      const nextStream = new MediaStream(existingTracks);
      if (!nextStream.getTracks().some((t) => t.id === event.track.id)) {
        nextStream.addTrack(event.track);
      }
      this.mediaStream = nextStream;
      this.emit("stream", this.mediaStream);
    };

    pc.ondatachannel = (event) => {
      if (event.channel.label === "input_v2" || event.channel.label === "input") {
        this.inputChannel.attachDataChannel(event.channel);
      } else if (event.channel.label === "sensors_v2") {
        this.sensorsChannel?.attachDataChannel(event.channel);
      }
    };

    pc.onconnectionstatechange = () => {
      switch (pc.connectionState) {
        case "connected":
          this.setState(RtcConnectionState.CONNECTED);
          break;
        case "disconnected":
          this.setState(RtcConnectionState.RECONNECTING);
          break;
        case "failed":
          this.setState(RtcConnectionState.FAILED);
          break;
        case "closed":
          this.setState(RtcConnectionState.CLOSED);
          break;
      }
    };
  }

  private startJsepStream(guid: string) {
    // Messages are chained rather than handled concurrently: setRemoteDescription
    // must complete before any candidate that follows it is added.
    this.streamCancelFn = subscribe(
      (signal) => this.rpc.ReceiveJsepMessageStream({ sessionId: guid }, signal),
      (jsep: JsepMessage) => {
        this.jsepProcessingChain = this.jsepProcessingChain
          .then(async () => {
            if (jsep.sdp) {
              await this.handleJsepMessage({
                type: jsep.sdp.type === SessionDescription_Type.ANSWER ? "answer" : "offer",
                sdp: jsep.sdp.sdp,
              });
            } else if (jsep.iceCandidate) {
              await this.handleJsepMessage({
                candidate: jsep.iceCandidate.candidate,
                sdpMid: jsep.iceCandidate.sdpMid,
                sdpMLineIndex: jsep.iceCandidate.sdpMLineIndex,
                usernameFragment: jsep.iceCandidate.usernameFragment,
              });
            }
          })
          .catch((err) => {
            console.error("Error processing incoming JSEP message:", err);
          });
      },
      (err: any) => {
        console.error("JSEP stream error:", err);
        this.setState(RtcConnectionState.FAILED);
        this.emit("error", err);
      }
    );
  }

  private injectLoopbackActiveCandidate(sdp: string): string {
    const candidateLine =
      "a=candidate:loopback-active 1 tcp 1000 127.0.0.1 9 typ host tcptype active\r\n";
    if (sdp.includes(candidateLine)) {
      return sdp;
    }
    const mid0Marker = "a=mid:0\r\n";
    if (sdp.includes(mid0Marker)) {
      return sdp.replace(mid0Marker, `${mid0Marker}${candidateLine}`);
    }
    return sdp.endsWith("\r\n")
      ? `${sdp}${candidateLine}`
      : `${sdp}\r\n${candidateLine}`;
  }
}
