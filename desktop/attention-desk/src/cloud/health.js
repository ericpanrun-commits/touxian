import { auth, currentIdentity, request } from './client';
import { validateHealthOverview } from '../../supabase/functions/eric-app-api/health_contract.js';

export async function readCloudOverview({ signal }) {
  const identity = currentIdentity();
  if (!identity || identity.offline) throw new Error('请联网后刷新 Health。');
  const scope = { owner: identity.owner, deviceId: identity.device.id };
  // request() verifies scope before and after completion, and never writes Dexie.
  return validateHealthOverview(await request('/health/overview', { signal, scope }));
}

export function subscribeHealthPrivacy(clear) {
  const scope = currentIdentity();
  const { data } = auth.auth.onAuthStateChange((event, session) => {
    if (event === 'SIGNED_OUT' || session && session.user.id !== scope?.owner) clear();
  });
  return () => data.subscription.unsubscribe();
}
