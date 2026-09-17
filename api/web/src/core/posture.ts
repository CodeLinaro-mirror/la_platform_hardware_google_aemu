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

import { GrpcWebTransport } from './transport';
import {
  PostureState,
  GetPostureStateRequest,
  StreamPostureStateRequest,
  UpdatePostureStateRequest,
} from '@android/emulator-services/environment/posture_service';
import { PostureType } from '@android/emulator-services/common/common';

export { PostureState, PostureType };

export interface PostureStateUpdate {
  posture?: PostureType;
  hingeAngleDegrees?: number;
}

/**
 * Headless API client for PostureService (Foldables & Hinge Angle).
 */
export class PostureClient {
  private readonly transport: GrpcWebTransport;

  constructor(transport: GrpcWebTransport) {
    this.transport = transport;
  }

  public async getPostureState(): Promise<PostureState> {
    const reqBytes = GetPostureStateRequest.encode({}).finish();
    const resBytes = await this.transport.request(
      'android.emulation.v2.environment.PostureService',
      'GetPostureState',
      reqBytes
    );
    return PostureState.decode(resBytes);
  }

  public streamPostureState(
    onState: (state: PostureState) => void,
    onError?: (err: any) => void
  ): () => void {
    const reqBytes = StreamPostureStateRequest.encode({}).finish();
    return this.transport.stream(
      'android.emulation.v2.environment.PostureService',
      'StreamPostureState',
      reqBytes,
      (bytes) => onState(PostureState.decode(bytes)),
      onError
    );
  }

  public streamPostureStateIterator(): AsyncIterable<PostureState> {
    const reqBytes = StreamPostureStateRequest.encode({}).finish();
    const byteStream = this.transport.serverStream(
      'android.emulation.v2.environment.PostureService',
      'StreamPostureState',
      reqBytes
    );

    return {
      async *[Symbol.asyncIterator]() {
        for await (const chunkBytes of byteStream) {
          yield PostureState.decode(chunkBytes);
        }
      },
    };
  }

  public async updatePostureState(update: PostureStateUpdate): Promise<PostureState> {
    const paths: string[] = [];
    const state: PostureState = {
      posture: update.posture ?? PostureType.POSTURE_TYPE_UNSPECIFIED,
      hingeAngleDegrees: update.hingeAngleDegrees ?? 0,
      timestampNanos: 0,
    };

    if (update.posture !== undefined) {
      paths.push('posture');
    }
    if (update.hingeAngleDegrees !== undefined) {
      paths.push('hinge_angle_degrees');
    }

    const req: UpdatePostureStateRequest = {
      postureState: state,
      updateMask: paths,
    };

    const reqBytes = UpdatePostureStateRequest.encode(req).finish();
    const resBytes = await this.transport.request(
      'android.emulation.v2.environment.PostureService',
      'UpdatePostureState',
      reqBytes
    );
    return PostureState.decode(resBytes);
  }

  public async setHingeAngle(degrees: number): Promise<PostureState> {
    return this.updatePostureState({ hingeAngleDegrees: degrees });
  }

  public async setDevicePosture(posture: PostureType): Promise<PostureState> {
    return this.updatePostureState({ posture });
  }
}