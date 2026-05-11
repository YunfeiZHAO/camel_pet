export type FocusCoachState = {
  focus_coach_enabled: boolean;
  focus_coach_interval_seconds: number;
  focus_coach_window_minutes: number;
  distracted_threshold_minutes: number;
  focus_coach_cooldown_seconds: number;
  focus_categories: string[];
  distraction_categories: string[];
};

export type Ready = {
  type: "ready";
  model: string;
  clipboard_enabled: boolean;
  nudges_enabled: boolean;
  screen_monitor_enabled: boolean;
  monitor_interval_seconds: number;
  has_api_key: boolean;
  init_error: string | null;
} & FocusCoachState;

export type ConfigAck = {
  type: "config_ack";
  model: string;
  clipboard_enabled: boolean;
  nudges_enabled: boolean;
  screen_monitor_enabled: boolean;
  monitor_interval_seconds: number;
  has_api_key: boolean;
  changed: boolean;
} & FocusCoachState;

export type AgentEvent =
  | Ready
  | ConfigAck
  | { type: "token"; text: string }
  | { type: "done" }
  | { type: "error"; message: string }
  | { type: "timer_fired"; id: number; message: string }
  | { type: "timer_cancelled"; id: number; ok: boolean }
  | { type: "timers"; items: { id: number; message: string; remaining_s: number }[] }
  | { type: "history_cleared" }
  | { type: "nudge" }
  | { type: "proactive"; text: string };

export type OutgoingConfig = {
  model?: string;
  api_key?: string;
  platform?: string;
  base_url?: string;
  clipboard_enabled?: boolean;
  nudges_enabled?: boolean;
  screen_monitor_enabled?: boolean;
  monitor_interval_seconds?: number;
  focus_coach_enabled?: boolean;
  focus_coach_interval_seconds?: number;
  focus_coach_window_minutes?: number;
  distracted_threshold_minutes?: number;
  focus_coach_cooldown_seconds?: number;
  focus_categories?: string[];
  distraction_categories?: string[];
};

export class AgentSocket {
  private ws: WebSocket | null = null;
  private url: string;
  private listeners = new Set<(e: AgentEvent) => void>();
  private reconnectTimer: number | null = null;

  constructor(url = "ws://127.0.0.1:8765/ws") {
    this.url = url;
  }

  connect() {
    if (this.ws && this.ws.readyState <= 1) return;
    this.ws = new WebSocket(this.url);
    this.ws.onmessage = (ev) => {
      try {
        const data = JSON.parse(ev.data) as AgentEvent;
        this.listeners.forEach((l) => l(data));
      } catch {
        // ignore
      }
    };
    this.ws.onclose = () => {
      if (this.reconnectTimer) return;
      this.reconnectTimer = window.setTimeout(() => {
        this.reconnectTimer = null;
        this.connect();
      }, 1500);
    };
    this.ws.onerror = () => this.ws?.close();
  }

  on(fn: (e: AgentEvent) => void) {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }

  private _sendRaw(payload: object): boolean {
    if (!this.ws || this.ws.readyState !== 1) return false;
    this.ws.send(JSON.stringify(payload));
    return true;
  }

  send(text: string): boolean {
    return this._sendRaw({ type: "user", text });
  }

  sendConfig(cfg: OutgoingConfig): boolean {
    return this._sendRaw({ type: "config", ...cfg });
  }

  clearHistory(): boolean {
    return this._sendRaw({ type: "clear_history" });
  }

  cancelTimer(id: number): boolean {
    return this._sendRaw({ type: "cancel_timer", id });
  }

  listTimers(): boolean {
    return this._sendRaw({ type: "list_timers" });
  }

  get ready() {
    return this.ws?.readyState === 1;
  }
}
