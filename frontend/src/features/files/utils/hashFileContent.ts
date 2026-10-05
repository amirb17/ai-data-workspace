export async function hashFileContent(file: File): Promise<string> {
  if (!crypto.subtle) throw new Error("Content verification requires a secure browser context (HTTPS or localhost).")
  const digest = await crypto.subtle.digest("SHA-256", await file.arrayBuffer())
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("")
}
