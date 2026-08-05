/** Cloudflare bindings for Howlscan edge worker */
interface Env {
  ASSETS: Fetcher;
  /** Backend explorer origin (VPS). Must NOT be the Worker hostname. */
  ORIGIN: string;
  /** off | mild | full — default trip level when cookie/header absent */
  TRIPPY_DEFAULT?: string;
}
