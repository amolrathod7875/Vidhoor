import { initializeApp } from "firebase/app";
import { getAuth } from "firebase/auth";

const firebaseConfig = {
  apiKey: "AIzaSyCEpJ_shshUSG-qrpCAwuPHNnVb0MjDxOg",
  authDomain: "vidhoor-18df7.firebaseapp.com",
  projectId: "vidhoor-18df7",
  storageBucket: "vidhoor-18df7.firebasestorage.app",
  messagingSenderId: "104415676953",
  appId: "1:104415676953:web:34e02a32984ea1d3c2f888",
  measurementId: "G-ZC5G82DEY9",
};

const app = initializeApp(firebaseConfig);
export const auth = getAuth(app);
export default app;
