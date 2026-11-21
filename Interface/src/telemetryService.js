// Interface/src/telemetryService.js
import mqtt from 'mqtt';
import { subscribeToLatestTelemetry } from './firebase';

/**
 * Subscribes to real-time telemetry from both MQTT WebSocket (instant, <100ms)
 * and Firebase Firestore (cloud persistence backup).
 *
 * @param {Function} onTelemetry Callback receiving formatted telemetry data
 * @param {Function} onConnectionChange Callback receiving connection status string
 * @returns {Function} Cleanup / unsubscribe function
 */
export function subscribeToWorkstationTelemetry(onTelemetry, onConnectionChange) {
  let isCleanedUp = false;
  let lastMqttTs = 0;
  let mqttClient = null;
  let firestoreUnsub = null;

  const updateStatus = (status) => {
    if (!isCleanedUp && onConnectionChange) {
      onConnectionChange(status);
    }
  };

  // Determine broker URL: Ensure WSS is used if the app is loaded over HTTPS
  const isHttps = typeof window !== 'undefined' && window.location.protocol === 'https:';
  let brokerUrl = import.meta.env.VITE_MQTT_BROKER_URL || 'wss://broker.emqx.io:8084/mqtt';
  
  if (isHttps && brokerUrl.startsWith('ws://')) {
    brokerUrl = 'wss://broker.emqx.io:8084/mqtt';
  }

  const topicTelemetry = import.meta.env.VITE_MQTT_TOPIC || 'smart_workstation/telemetry';
  const topicNode1 = 'smart_workstation/node1';
  const topicNode2 = 'smart_workstation/node2';

  try {
    updateStatus('Connecting...');
    mqttClient = mqtt.connect(brokerUrl, {
      clientId: `web-dash-${Math.random().toString(16).substring(2, 10)}`,
      clean: true,
      connectTimeout: 5000,
      reconnectPeriod: 3000,
      keepalive: 30,
    });

    mqttClient.on('connect', () => {
      if (isCleanedUp) return;
      updateStatus('MQTT Live');
      mqttClient.subscribe([topicTelemetry, topicNode1, topicNode2], { qos: 0 }, (err) => {
        if (err) {
          console.warn('MQTT subscription error:', err);
        }
      });
    });

    mqttClient.on('message', (receivedTopic, payload) => {
      if (isCleanedUp) return;
      try {
        const raw = JSON.parse(payload.toString());
        if (receivedTopic === topicTelemetry) {
          lastMqttTs = Date.now();
          updateStatus('MQTT Live');
          onTelemetry({ ...raw, _source: 'mqtt', _gateway_rx: Date.now() });
        } else if (receivedTopic === topicNode1) {
          onTelemetry({ _type: 'node1_heartbeat', _node1_raw: raw, _rx_node1: Date.now() });
        } else if (receivedTopic === topicNode2) {
          onTelemetry({ _type: 'node2_heartbeat', _node2_raw: raw, _rx_node2: Date.now() });
        }
      } catch (e) {
        console.warn('Failed to parse MQTT message JSON:', e);
      }
    });

    mqttClient.on('error', (err) => {
      console.warn('MQTT WebSocket connection notice:', err.message);
      if (Date.now() - lastMqttTs > 5000) {
        updateStatus('Cloud Sync');
      }
    });

    mqttClient.on('close', () => {
      if (Date.now() - lastMqttTs > 5000) {
        updateStatus('Cloud Sync');
      }
    });
  } catch (err) {
    console.warn('Could not initialize MQTT WebSocket client:', err);
    updateStatus('Cloud Sync');
  }

  // Also subscribe to Firebase Firestore as persistent backup
  if (import.meta.env.VITE_FIREBASE_PROJECT_ID) {
    firestoreUnsub = subscribeToLatestTelemetry(
      (data) => {
        if (isCleanedUp) return;
        // If MQTT is already actively streaming (<3 seconds old), let MQTT drive the UI
        if (Date.now() - lastMqttTs < 3000) {
          return;
        }
        updateStatus('Cloud Sync');
        onTelemetry({ ...data, _source: 'firestore' });
      },
      () => {
        if (Date.now() - lastMqttTs > 5000) {
          updateStatus('Offline');
        }
      }
    );
  }

  return () => {
    isCleanedUp = true;
    if (mqttClient) {
      try {
        mqttClient.end(true);
      } catch (_) {}
    }
    if (firestoreUnsub) {
      try {
        firestoreUnsub();
      } catch (_) {}
    }
  };
}
