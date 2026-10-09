export function remainingSeconds(status, receivedAt, now) {
  if (!status?.enabled || !status.pairing_available || !status.pairing_id) return 0;
  return Math.max(0, Math.ceil(status.pairing_expires_in - Math.max(0, now - receivedAt) / 1000));
}
export function qrImagePath(status) {
  if (!status?.enabled || !status.pairing_available || !status.pairing_id || !status.server_epoch) return null;
  return `/api/personal/collector/qr?pairing_id=${encodeURIComponent(status.pairing_id)}&epoch=${encodeURIComponent(status.server_epoch)}`;
}
export function expiryLabel(seconds) {
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
}
