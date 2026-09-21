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

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { RtcController, RtcConnectionState } from '../src/core/rtc_controller';
import { InputChannel } from '../src/core/input_channel';
import {
  JsepMessage,
  RtcStreamResponse,
} from '@android/emulator-services/webrtc_service';
import { FakeStream } from './fake_transport';

// Mock WebRTC PeerConnection
class MockRTCPeerConnection {
  public localDescription: RTCSessionDescriptionInit | null = null;
  public remoteDescription: RTCSessionDescriptionInit | null = null;
  public iceConnectionState: RTCIceConnectionState = 'new';
  public connectionState: RTCPeerConnectionState = 'new';
  public signalingState: RTCSignalingState = 'stable';

  public ontrack: ((ev: any) => void) | null = null;
  public onicecandidate: ((ev: any) => void) | null = null;
  public ondatachannel: ((ev: any) => void) | null = null;
  public onconnectionstatechange: ((ev: any) => void) | null = null;

  public addedIceCandidates: any[] = [];
  public closed = false;

  async setRemoteDescription(desc: RTCSessionDescriptionInit) {
    this.remoteDescription = desc;
  }

  async createOffer(): Promise<RTCSessionDescriptionInit> {
    return { type: 'offer', sdp: 'v=0\r\no=mock-client-offer' };
  }

  async createAnswer(): Promise<RTCSessionDescriptionInit> {
    return { type: 'answer', sdp: 'v=0\r\no=mock-answer' };
  }

  async setLocalDescription(desc: RTCSessionDescriptionInit) {
    this.localDescription = desc;
  }

  async addIceCandidate(candidate: any) {
    this.addedIceCandidates.push(candidate);
  }

  addTransceiver(trackOrKind: any, init?: any) {
    return {};
  }

  createDataChannel(label: string, init?: any) {
    return {
      label,
      readyState: 'connecting',
      send: vi.fn(),
      close: vi.fn(),
    };
  }

  close() {
    this.closed = true;
    this.connectionState = 'closed';
  }
}

// Mock RtcService transport. It implements the members the generated
// RtcServiceClientImpl calls, so the encoding and decoding under test is the
// real generated code.
class MockRtcTransport {
  public requestStreamMock = vi.fn();
  public sendJsepMock = vi.fn();
  public readonly jsepStream = new FakeStream();

  async request(service: string, method: string, data: Uint8Array): Promise<Uint8Array> {
    if (method === 'RequestRtcStream') {
      return this.requestStreamMock(data);
    }
    if (method === 'SendJsepMessage') {
      return this.sendJsepMock(data);
    }
    return new Uint8Array(0);
  }

  serverStreamingRequest(
    _service: string,
    _method: string,
    _data: Uint8Array,
    signal?: AbortSignal
  ): AsyncIterable<Uint8Array> {
    return this.jsepStream.iterate(signal);
  }
}

describe('RtcController (TDD)', () => {
  let mockPeerConnection: MockRTCPeerConnection;
  let mockTransport: MockRtcTransport;
  let inputChannel: InputChannel;
  let rtcController: RtcController;

  beforeEach(() => {
    mockPeerConnection = new MockRTCPeerConnection();
    mockTransport = new MockRtcTransport();
    inputChannel = new InputChannel();

    mockTransport.requestStreamMock.mockResolvedValue(
      RtcStreamResponse.encode(
        RtcStreamResponse.fromPartial({ handle: { sessionId: 'session-123' } })
      ).finish()
    );

    mockTransport.sendJsepMock.mockResolvedValue(new Uint8Array(0));

    rtcController = new RtcController({
      transport: mockTransport as any,
      inputChannel,
      createPeerConnection: () => mockPeerConnection as any,
    });
  });

  it('starts in disconnected state and transitions to connecting on connect()', async () => {
    expect(rtcController.state).toBe(RtcConnectionState.DISCONNECTED);

    const stateChanges: RtcConnectionState[] = [];
    rtcController.on('stateChange', (state) => stateChanges.push(state));

    const connectPromise = rtcController.connect();
    expect(rtcController.state).toBe(RtcConnectionState.CONNECTING);
    expect(stateChanges).toContain(RtcConnectionState.CONNECTING);

    await connectPromise;
    expect(mockTransport.requestStreamMock).toHaveBeenCalledTimes(1);
  });

  it('creates and sends client SDP offer, then sets remote description upon receiving server SDP answer', async () => {
    await rtcController.connect();

    // Verify client sent its local offer
    expect(mockPeerConnection.localDescription).toEqual({
      type: 'offer',
      sdp: 'v=0\r\no=mock-client-offer\r\na=candidate:loopback-active 1 tcp 1000 127.0.0.1 9 typ host tcptype active\r\n',
    });
    expect(mockTransport.sendJsepMock).toHaveBeenCalledTimes(1);

    // Simulate receiving server's answer
    const answerSdp = 'v=0\r\no=mock-server-answer';
    await rtcController.handleJsepMessage({
      sdp: answerSdp,
      type: 'answer',
    });

    expect(mockPeerConnection.remoteDescription).toEqual({
      type: 'answer',
      sdp: answerSdp,
    });
  });

  it('injects loopback active TCP candidate immediately after a=mid:0 and is idempotent', async () => {
    const candidateLine =
      'a=candidate:loopback-active 1 tcp 1000 127.0.0.1 9 typ host tcptype active\r\n';
    mockPeerConnection.createOffer = async () => ({
      type: 'offer',
      sdp: 'v=0\r\nm=video 9 UDP/TLS/RTP/SAVPF 96\r\na=mid:0\r\na=sendrecv\r\n',
    });

    await rtcController.connect();

    expect(mockPeerConnection.localDescription?.sdp).toBe(
      `v=0\r\nm=video 9 UDP/TLS/RTP/SAVPF 96\r\na=mid:0\r\n${candidateLine}a=sendrecv\r\n`
    );

    // Verify idempotency when candidate line is already present
    const injectFn = (rtcController as any).injectLoopbackActiveCandidate.bind(rtcController);
    const once = injectFn(mockPeerConnection.localDescription!.sdp!);
    expect(once).toBe(mockPeerConnection.localDescription?.sdp);
  });

  it('adds remote ICE candidate to PeerConnection', async () => {
    await rtcController.connect();
    await rtcController.handleJsepMessage({
      sdp: 'v=0\r\no=mock-server-answer',
      type: 'answer',
    });

    await rtcController.handleJsepMessage({
      candidate: 'candidate:1 1 UDP 2130706431 192.168.1.1 5000 typ host',
      sdpMid: '0',
      sdpMLineIndex: 0,
    });

    expect(mockPeerConnection.addedIceCandidates.length).toBe(1);
    expect(mockPeerConnection.addedIceCandidates[0].candidate).toContain('192.168.1.1');
  });

  it('sends local ICE candidate to server via SendJsepMessage', async () => {
    await rtcController.connect();

    if (mockPeerConnection.onicecandidate) {
      mockPeerConnection.onicecandidate({
        candidate: {
          candidate: 'candidate:2 1 UDP 2130706431 127.0.0.1 6000 typ host',
          sdpMid: '0',
          sdpMLineIndex: 0,
        },
      });
    }

    // 1 call for SDP offer, 1 call for ICE candidate
    expect(mockTransport.sendJsepMock).toHaveBeenCalledTimes(2);
  });

  it('attaches incoming input DataChannel to inputChannel', async () => {
    await rtcController.connect();
    expect(inputChannel.isReady).toBe(false);

    const mockDataChannel = {
      label: 'input',
      readyState: 'open',
      binaryType: 'arraybuffer',
      send: vi.fn(),
    };

    if (mockPeerConnection.ondatachannel) {
      mockPeerConnection.ondatachannel({
        channel: mockDataChannel,
      });
    }

    expect(inputChannel.isReady).toBe(true);
  });

  it('attaches incoming input_v2 DataChannel to inputChannel', async () => {
    await rtcController.connect();
    expect(inputChannel.isReady).toBe(false);

    const mockDataChannel = {
      label: 'input_v2',
      readyState: 'open',
      binaryType: 'arraybuffer',
      send: vi.fn(),
    };

    if (mockPeerConnection.ondatachannel) {
      mockPeerConnection.ondatachannel({
        channel: mockDataChannel,
      });
    }

    expect(inputChannel.isReady).toBe(true);
  });

  it('buffers remote ICE candidates received before SDP answer and flushes them after setRemoteDescription', async () => {
    await rtcController.connect();

    // Candidate arrives before SDP answer
    await rtcController.handleJsepMessage({
      candidate: 'candidate:early-1 1 UDP 2130706431 127.0.0.1 50000 typ host',
      sdpMid: '0',
      sdpMLineIndex: 0,
    });

    expect(mockPeerConnection.addedIceCandidates.length).toBe(0);

    // Now SDP answer arrives
    await rtcController.handleJsepMessage({
      sdp: 'v=0\r\no=mock-server-answer',
      type: 'answer',
    });

    expect(mockPeerConnection.addedIceCandidates.length).toBe(1);
    expect(mockPeerConnection.addedIceCandidates[0].candidate).toContain('early-1');
  });

  it('serializes incoming ReceiveJsepMessageStream callbacks so slow setRemoteDescription finishes before candidate', async () => {
    const events: string[] = [];
    mockPeerConnection.setRemoteDescription = async (desc: RTCSessionDescriptionInit) => {
      await new Promise((r) => setTimeout(r, 15));
      mockPeerConnection.remoteDescription = desc;
      events.push('setRemoteDescription');
    };
    mockPeerConnection.addIceCandidate = async (cand: any) => {
      mockPeerConnection.addedIceCandidates.push(cand);
      events.push('addIceCandidate');
    };

    await rtcController.connect();

    // Both arrive back-to-back: the candidate must still wait for the answer.
    mockTransport.jsepStream.push(
      JsepMessage.encode(
        JsepMessage.fromPartial({
          handle: { sessionId: 'session-123' },
          sdp: { type: 2 /* ANSWER */, sdp: 'v=0\r\no=delayed-answer' },
        })
      ).finish()
    );
    mockTransport.jsepStream.push(
      JsepMessage.encode(
        JsepMessage.fromPartial({
          handle: { sessionId: 'session-123' },
          iceCandidate: {
            candidate: 'candidate:queued 1 tcp 1000 127.0.0.1 50000 typ host tcptype passive',
            sdpMid: '0',
            sdpMLineIndex: 0,
          },
        })
      ).finish()
    );

    // Let the subscription hand both messages to the processing chain, then let
    // the chain drain.
    for (let i = 0; i < 5; i++) {
      await new Promise((r) => setTimeout(r, 0));
    }
    await (rtcController as any).jsepProcessingChain;

    expect(events).toEqual(['setRemoteDescription', 'addIceCandidate']);
    expect(mockPeerConnection.addedIceCandidates.length).toBe(1);
  });

  it('clones MediaStream on incremental ontrack events so audio and video tracks trigger distinct stream emissions', async () => {
    // Provide a lightweight global MediaStream mock for Node environment
    const prevMediaStream = (globalThis as any).MediaStream;
    class MockMediaStream {
      private tracks: any[];
      constructor(existing?: any[]) {
        this.tracks = existing ? [...existing] : [];
      }
      getTracks() {
        return [...this.tracks];
      }
      addTrack(track: any) {
        this.tracks.push(track);
      }
    }
    (globalThis as any).MediaStream = MockMediaStream;

    try {
      await rtcController.connect();
      const emittedStreams: any[] = [];
      rtcController.on('stream', (s) => emittedStreams.push(s));

      mockPeerConnection.ontrack?.({ track: { id: 'video-0', kind: 'video' } });
      mockPeerConnection.ontrack?.({ track: { id: 'audio-0', kind: 'audio' } });

      expect(emittedStreams.length).toBe(2);
      expect(emittedStreams[0]).not.toBe(emittedStreams[1]);
      expect(emittedStreams[0].getTracks().length).toBe(1);
      expect(emittedStreams[1].getTracks().length).toBe(2);
    } finally {
      (globalThis as any).MediaStream = prevMediaStream;
    }
  });

  it('disconnect() closes PeerConnection and transitions state to disconnected', async () => {
    await rtcController.connect();
    rtcController.disconnect();

    expect(mockPeerConnection.closed).toBe(true);
    expect(rtcController.state).toBe(RtcConnectionState.DISCONNECTED);
  });
});
