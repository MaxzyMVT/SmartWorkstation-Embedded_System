// Interface/src/firebase.js
import { initializeApp } from 'firebase/app';
import { getFirestore, collection, query, orderBy, limit, onSnapshot } from 'firebase/firestore';

const firebaseConfig = {
  apiKey: import.meta.env.VITE_FIREBASE_API_KEY,
  authDomain: import.meta.env.VITE_FIREBASE_AUTH_DOMAIN,
  projectId: import.meta.env.VITE_FIREBASE_PROJECT_ID,
  storageBucket: import.meta.env.VITE_FIREBASE_STORAGE_BUCKET,
  messagingSenderId: import.meta.env.VITE_FIREBASE_MESSAGING_SENDER_ID,
  appId: import.meta.env.VITE_FIREBASE_APP_ID,
};

// Initialize Firebase
const app = initializeApp(firebaseConfig);
export const db = getFirestore(app);

/**
 * Subscribes in real-time to the latest telemetry uploaded by the Gateway.
 * @param {Function} onData Callback receiving latest telemetry payload
 * @param {Function} onError Optional error callback
 * @returns {Function} Unsubscribe function
 */
export function subscribeToLatestTelemetry(onData, onError) {
  const collectionName = import.meta.env.VITE_FIRESTORE_COLLECTION || 'telemetry';
  const q = query(collection(db, collectionName), orderBy('timestamp', 'desc'), limit(1));

  return onSnapshot(
    q,
    (snapshot) => {
      if (!snapshot.empty) {
        const latestDoc = snapshot.docs[0].data();
        onData(latestDoc);
      }
    },
    (err) => {
      console.warn('Firestore subscription notice (using local data):', err);
      if (onError) onError(err);
    }
  );
}
