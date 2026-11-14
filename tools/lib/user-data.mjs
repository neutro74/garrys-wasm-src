// Per-user state inside a GMod install: addons, saved data, caches, databases, logs, personal configs. Never
// packaged or published; stock game files only. Paths are relative to the game root (game/ in a package).
export const USER_PATHS = [
  'garrysmod/addons', 'garrysmod/data', 'garrysmod/cache', 'garrysmod/saves', 'garrysmod/screenshots',
  'garrysmod/demos', 'garrysmod/dupes', 'garrysmod/download', 'garrysmod/downloads', 'garrysmod/workshop',
  'garrysmod/cfg/config.cfg', 'garrysmod/cfg/autoexec.cfg', 'garrysmod/GameState.txt', 'garrysmod/voice_ban.dt', 'chromium.log',
];
export function isUserData(rel) {
  const p = rel.replaceAll('\\', '/').replace(/^\/+/, '').toLowerCase();
  if (/\.(db|log|sqlite3?)$/.test(p)) return true;
  return USER_PATHS.some(u => { u = u.toLowerCase(); return p === u || p.startsWith(u + '/'); });
}
