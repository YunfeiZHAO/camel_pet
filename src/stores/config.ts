import { defineStore } from "pinia";
import { ref, watch } from "vue";

const LS_KEY = "camel-pet.config.v1";

type Persisted = {
  model: string;
  apiKey: string;
  platform: string;
  baseUrl: string;
  clipboardEnabled: boolean;
  nudgesEnabled: boolean;
  screenMonitorEnabled: boolean;
  monitorIntervalSeconds: number;
  focusCoachEnabled: boolean;
  focusCoachIntervalSeconds: number;
  focusCoachWindowMinutes: number;
  distractedThresholdMinutes: number;
  focusCoachCooldownSeconds: number;
  focusCategories: string[];
  distractionCategories: string[];
};

const DEFAULTS: Persisted = {
  model: "MiniMax-M2.7",
  apiKey: "",
  platform: "minmax",
  baseUrl: "",
  clipboardEnabled: false,
  nudgesEnabled: false,
  screenMonitorEnabled: false,
  monitorIntervalSeconds: 300,
  focusCoachEnabled: false,
  focusCoachIntervalSeconds: 300,
  focusCoachWindowMinutes: 30,
  distractedThresholdMinutes: 15,
  focusCoachCooldownSeconds: 900,
  focusCategories: ["coding", "working", "reading", "learning", "design", "meeting"],
  distractionCategories: ["video", "gaming", "social_media", "browsing"],
};

function load(): Persisted {
  try {
    const raw = localStorage.getItem(LS_KEY);
    if (!raw) return { ...DEFAULTS };
    const parsed = JSON.parse(raw) as Partial<Persisted>;
    return { ...DEFAULTS, ...parsed };
  } catch {
    return { ...DEFAULTS };
  }
}

export const useConfigStore = defineStore("config", () => {
  const initial = load();
  const model = ref(initial.model);
  const apiKey = ref(initial.apiKey);
  const platform = ref(initial.platform);
  const baseUrl = ref(initial.baseUrl);
  const clipboardEnabled = ref(initial.clipboardEnabled);
  const nudgesEnabled = ref(initial.nudgesEnabled);
  const screenMonitorEnabled = ref(initial.screenMonitorEnabled);
  const monitorIntervalSeconds = ref(initial.monitorIntervalSeconds);
  const focusCoachEnabled = ref(initial.focusCoachEnabled);
  const focusCoachIntervalSeconds = ref(initial.focusCoachIntervalSeconds);
  const focusCoachWindowMinutes = ref(initial.focusCoachWindowMinutes);
  const distractedThresholdMinutes = ref(initial.distractedThresholdMinutes);
  const focusCoachCooldownSeconds = ref(initial.focusCoachCooldownSeconds);
  const focusCategories = ref<string[]>([...initial.focusCategories]);
  const distractionCategories = ref<string[]>([...initial.distractionCategories]);

  // Server is the source of truth after handshake; these mirror that.
  const serverHasApiKey = ref(false);
  const serverModel = ref(initial.model);

  function persist() {
    const snap: Persisted = {
      model: model.value,
      apiKey: apiKey.value,
      platform: platform.value,
      baseUrl: baseUrl.value,
      clipboardEnabled: clipboardEnabled.value,
      nudgesEnabled: nudgesEnabled.value,
      screenMonitorEnabled: screenMonitorEnabled.value,
      monitorIntervalSeconds: monitorIntervalSeconds.value,
      focusCoachEnabled: focusCoachEnabled.value,
      focusCoachIntervalSeconds: focusCoachIntervalSeconds.value,
      focusCoachWindowMinutes: focusCoachWindowMinutes.value,
      distractedThresholdMinutes: distractedThresholdMinutes.value,
      focusCoachCooldownSeconds: focusCoachCooldownSeconds.value,
      focusCategories: [...focusCategories.value],
      distractionCategories: [...distractionCategories.value],
    };
    try {
      localStorage.setItem(LS_KEY, JSON.stringify(snap));
    } catch {
      // storage full / disabled — ignore
    }
  }

  watch(
    [
      model,
      apiKey,
      platform,
      baseUrl,
      clipboardEnabled,
      nudgesEnabled,
      screenMonitorEnabled,
      monitorIntervalSeconds,
      focusCoachEnabled,
      focusCoachIntervalSeconds,
      focusCoachWindowMinutes,
      distractedThresholdMinutes,
      focusCoachCooldownSeconds,
      focusCategories,
      distractionCategories,
    ],
    persist,
    { deep: true },
  );

  return {
    model,
    apiKey,
    platform,
    baseUrl,
    clipboardEnabled,
    nudgesEnabled,
    screenMonitorEnabled,
    monitorIntervalSeconds,
    focusCoachEnabled,
    focusCoachIntervalSeconds,
    focusCoachWindowMinutes,
    distractedThresholdMinutes,
    focusCoachCooldownSeconds,
    focusCategories,
    distractionCategories,
    serverHasApiKey,
    serverModel,
  };
});
