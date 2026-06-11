// Download links for voicetuner releases
// These are fallback values - link to releases page if API fails
export const LATEST_VERSION = 'v0.1.0';

export const GITHUB_REPO = 'https://github.com/umadevi16177/voice_tuner';
export const GITHUB_RELEASES_PAGE = `${GITHUB_REPO}/releases`;
export const DONATE_URL = GITHUB_REPO;
export const SPONSOR_CHECKOUT_URL = '';
export const SPONSOR_CONTACT_EMAIL = '';

export const DOWNLOAD_LINKS = {
  macArm: GITHUB_RELEASES_PAGE,
  macIntel: GITHUB_RELEASES_PAGE,
  windows: GITHUB_RELEASES_PAGE,
  linux: GITHUB_RELEASES_PAGE,
} as const;

// Export function to get dynamic download links
export { getLatestRelease } from './releases';
