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

// Mock RtcService transport
class MockRtcTransport {
  public requestStreamMock = vi.fn();
  public sendJsepMock = vi.fn();
  public receiveStreamGenerator: any;

  async request(service: string, method: string, data: Uint8Array): Promise<Uint8Array> {
    if (method === 'RequestRtcStream') {
      return this.requestStreamMock(data);
    }
    if (method === 'SendJsepMessage') {
      return this.sendJsepMock(data);
    }
    return new Uint8Array(0);
  }

  public stream = vi.fn().mockReturnValue(() => {});
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

    // Default mock response for RequestRtcStream
    mockTransport.requestStreamMock.mockResolvedValue(
      // RtcStreamResponse with handle { guid: "session-123" } and 1 STUN server
      new Uint8Array([
        0x0A, 0x0D, 0x0A, 0x0B, 0x73, 0x65, 0x73, 0x73, 0x69, 0x6F, 0x6E, 0x2D, 0x31, 0x32, 0x33,
      ])
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
      sdp: 'v=0\r\no=mock-client-offer',
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

  it('disconnect() closes PeerConnection and transitions state to disconnected', async () => {
    await rtcController.connect();
    rtcController.disconnect();

    expect(mockPeerConnection.closed).toBe(true);
    expect(rtcController.state).toBe(RtcConnectionState.DISCONNECTED);
  });
});
