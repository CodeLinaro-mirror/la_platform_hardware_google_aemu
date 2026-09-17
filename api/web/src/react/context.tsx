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

import { createContext, useContext, ReactNode, useMemo } from 'react';
import { DeviceClient } from '../core/client';
import { AuthProvider } from '../core/auth';

const DeviceContext = createContext<DeviceClient | null>(null);

export interface DeviceProviderProps {
  /**
   * Existing pre-configured DeviceClient instance.
   */
  client?: DeviceClient;

  /**
   * gRPC-Web proxy / server URI endpoint (e.g. "http://localhost:8085").
   * Used if client is not explicitly provided.
   */
  uri?: string;

  /**
   * Static JWT authentication token string.
   */
  token?: string;

  /**
   * Static or dynamic JWT auth provider.
   */
  auth?: AuthProvider;

  children: ReactNode;
}

/**
 * Top-level React Context Provider for Android Device Streaming.
 */
export function DeviceProvider({ client, uri, token, auth, children }: DeviceProviderProps) {
  const instance = useMemo(() => {
    if (client) return client;
    if (uri) {
      return DeviceClient.create({
        endpoint: uri,
        authProvider: auth || token,
      });
    }
    throw new Error('DeviceProvider requires either a "client" or a "uri" prop.');
  }, [client, uri, token, auth]);

  return (
    <DeviceContext.Provider value={instance}>
      {children}
    </DeviceContext.Provider>
  );
}

/**
 * Hook to access the root DeviceClient instance from context.
 */
export function useDeviceClient(): DeviceClient {
  const client = useContext(DeviceContext);
  if (!client) {
    throw new Error('useDeviceClient must be used within a <DeviceProvider>');
  }
  return client;
}

// Backward compatibility aliases
export {
  DeviceProvider as AquariumProvider,
  useDeviceClient as useAquariumClient,
  type DeviceProviderProps as AquariumProviderProps,
};
