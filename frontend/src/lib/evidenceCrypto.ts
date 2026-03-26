const LOCAL_KEY_STORAGE_KEY = "vidhoor_evidence_key_b64";
const AES_ALGORITHM = "AES-GCM";

const arrayBufferToBase64 = (buffer: ArrayBuffer): string => {
  const bytes = new Uint8Array(buffer);
  let binary = "";
  const chunkSize = 0x8000;

  for (let index = 0; index < bytes.length; index += chunkSize) {
    const chunk = bytes.subarray(index, index + chunkSize);
    binary += String.fromCharCode(...chunk);
  }

  return btoa(binary);
};

const base64ToUint8Array = (value: string): Uint8Array => {
  const binary = atob(value);
  const output = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) {
    output[index] = binary.charCodeAt(index);
  }
  return output;
};

const bytesToHex = (bytes: Uint8Array): string =>
  Array.from(bytes)
    .map((item) => item.toString(16).padStart(2, "0"))
    .join("");

const toArrayBuffer = (bytes: Uint8Array): ArrayBuffer =>
  bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer;

const getOrCreateRawKey = (): Uint8Array => {
  const existing = window.localStorage.getItem(LOCAL_KEY_STORAGE_KEY);
  if (existing) {
    return base64ToUint8Array(existing);
  }

  const generated = new Uint8Array(32);
  window.crypto.getRandomValues(generated);
  window.localStorage.setItem(LOCAL_KEY_STORAGE_KEY, arrayBufferToBase64(generated.buffer));
  return generated;
};

const importAesKey = async (rawKey: Uint8Array): Promise<CryptoKey> =>
  window.crypto.subtle.importKey("raw", toArrayBuffer(rawKey), { name: AES_ALGORITHM }, false, ["encrypt", "decrypt"]);

const buildKeyId = async (rawKey: Uint8Array): Promise<string> => {
  const digest = await window.crypto.subtle.digest("SHA-256", toArrayBuffer(rawKey));
  const digestBytes = new Uint8Array(digest).slice(0, 8);
  return `browser-local-${bytesToHex(digestBytes)}`;
};

export interface EncryptedUploadPayload {
  encryptedPayloadB64: string;
  ivB64: string;
  encryptionAlg: string;
  keyId: string;
}

export const decryptEvidencePayload = async (
  encryptedPayloadB64: string,
  ivB64: string,
): Promise<Blob> => {
  const rawKey = getOrCreateRawKey();
  const key = await importAesKey(rawKey);
  const encryptedBytes = base64ToUint8Array(encryptedPayloadB64);
  const ivBytes = base64ToUint8Array(ivB64);

  const decrypted = await window.crypto.subtle.decrypt(
    {
      name: AES_ALGORITHM,
      iv: toArrayBuffer(ivBytes),
    },
    key,
    toArrayBuffer(encryptedBytes),
  );

  return new Blob([decrypted]);
};

export const encryptFileForUpload = async (file: File): Promise<EncryptedUploadPayload> => {
  const rawKey = getOrCreateRawKey();
  const key = await importAesKey(rawKey);
  const keyId = await buildKeyId(rawKey);

  const iv = new Uint8Array(12);
  window.crypto.getRandomValues(iv);

  const fileBuffer = await file.arrayBuffer();
  const encrypted = await window.crypto.subtle.encrypt(
    {
      name: AES_ALGORITHM,
      iv,
    },
    key,
    fileBuffer,
  );

  return {
    encryptedPayloadB64: arrayBufferToBase64(encrypted),
    ivB64: arrayBufferToBase64(iv.buffer),
    encryptionAlg: "AES-GCM-256",
    keyId,
  };
};
