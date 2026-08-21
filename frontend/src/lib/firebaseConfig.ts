import { initializeApp } from "firebase/app";
import { getAuth } from "firebase/auth";

const firebaseConfig = {
  apiKey: "AIzaSyD6zw7Vvn_EKu-0x6sKAbM24iZzS5YXsoQ",
  authDomain: "vidhoor-7875.firebaseapp.com",
  projectId: "vidhoor-7875",
  storageBucket: "vidhoor-7875.firebasestorage.app",
  messagingSenderId: "384405187202",
  appId: "1:384405187202:web:ad18a4ee0992bafe9f229b",
};

const app = initializeApp(firebaseConfig);
export const auth = getAuth(app);
export default app;
