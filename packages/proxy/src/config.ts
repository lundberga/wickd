export interface UpstreamConfig {
  readonly openai: string;
  readonly anthropic: string;
  readonly google: string;
}

export interface ProxyConfig {
  readonly port: number;
  readonly host: string;
  readonly dbPath: string;
  readonly runName: string;
  readonly idleTimeoutMs: number;
  readonly upstream: UpstreamConfig;
}

export const DEFAULT_UPSTREAM: UpstreamConfig = {
  openai: "https://api.openai.com",
  anthropic: "https://api.anthropic.com",
  google: "https://generativelanguage.googleapis.com",
};

export const DEFAULT_PORT = 4319;
export const DEFAULT_HOST = "127.0.0.1";
export const DEFAULT_RUN_NAME = "wickd-proxy";
export const DEFAULT_IDLE_TIMEOUT_MS = 30_000;

export const DEFAULT_CONFIG: Omit<ProxyConfig, "dbPath"> = {
  port: DEFAULT_PORT,
  host: DEFAULT_HOST,
  runName: DEFAULT_RUN_NAME,
  idleTimeoutMs: DEFAULT_IDLE_TIMEOUT_MS,
  upstream: DEFAULT_UPSTREAM,
};
