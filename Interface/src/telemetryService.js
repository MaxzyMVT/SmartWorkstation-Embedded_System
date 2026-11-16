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

  const topic = import.meta.env.VITE_MQTT_TOPIC || 'smart_workstation/telemetry';

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
      mqttClient.subscribe(topic, { qos: 0 }, (err) => {
        if (err) {
          console.warn('MQTT subscription error:', err);
        }
      });
    });

    mqttClient.on('message', (receivedTopic, payload) => {
      if (isCleanedUp || receivedTopic !== topic) return;
      try {
        const raw = JSON.parse(payload.toString());
        lastMqttTs = Date.now();
        updateStatus('MQTT Live');
        onTelemetry({ ...raw, _source: 'mqtt' });
      } catch (e) {
        console.warn('Failed to parse MQTT telemetry JSON:', e);
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
