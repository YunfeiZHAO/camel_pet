<script setup lang="ts">
import { computed, ref } from "vue";
import { useConfigStore } from "../stores/config";

const emit = defineEmits<{
  (e: "close"): void;
  (e: "apply"): void;
  (e: "clearHistory"): void;
}>();

const cfg = useConfigStore();
const showKey = ref(false);
const dirty = ref(false);

function markDirty() {
  dirty.value = true;
}

function apply() {
  emit("apply");
  dirty.value = false;
}

function clearHistory() {
  if (confirm("Wipe the camel's memory? This deletes all stored chat history.")) {
    emit("clearHistory");
  }
}

const MODELS = [
  { value: "MiniMax-M2.7", label: "MiniMax M2.7" },
  { value: "claude-haiku-4-5", label: "Claude Haiku 4.5 (fast)" },
  { value: "claude-sonnet-4-6", label: "Claude Sonnet 4.6 (balanced)" },
  { value: "claude-opus-4-7", label: "Claude Opus 4.7 (smart)" },
];

const PLATFORMS = [
  { value: "minmax", label: "MiniMax (Anthropic-compatible)" },
  { value: "anthropic", label: "Anthropic" },
  { value: "openai_compatible", label: "OpenAI Compatible" },
];

// Mirrors agent/camel_pet_agent/vision_agent.py:ActivityStatus. Keep in sync.
const ALL_CATEGORIES = [
  "coding",
  "working",
  "reading",
  "learning",
  "design",
  "meeting",
  "communication",
  "music",
  "browsing",
  "video",
  "gaming",
  "social_media",
  "idle",
  "other",
];

type CatState = "focus" | "distracted" | "neutral";

function catState(cat: string): CatState {
  if (cfg.focusCategories.includes(cat)) return "focus";
  if (cfg.distractionCategories.includes(cat)) return "distracted";
  return "neutral";
}

function cycleCategory(cat: string) {
  const state = catState(cat);
  const focus = cfg.focusCategories.filter((c: string) => c !== cat);
  const dist = cfg.distractionCategories.filter((c: string) => c !== cat);
  if (state === "neutral") {
    focus.push(cat);
  } else if (state === "focus") {
    dist.push(cat);
  }
  // distracted -> neutral = just omit from both
  cfg.focusCategories = focus;
  cfg.distractionCategories = dist;
  markDirty();
}

const coachSummary = computed(() => {
  const f = cfg.focusCategories.length;
  const d = cfg.distractionCategories.length;
  return `${f} focus · ${d} distracted · ${ALL_CATEGORIES.length - f - d} ignored`;
});
</script>

<template>
  <div class="panel" @click.stop>
    <div class="header">
      <span>Settings</span>
      <button class="close" @click="emit('close')">×</button>
    </div>
    <div class="body">
      <label class="field">
        <span class="field-label">Platform</span>
        <select v-model="cfg.platform" @change="markDirty">
          <option v-for="p in PLATFORMS" :key="p.value" :value="p.value">{{ p.label }}</option>
        </select>
      </label>

      <label class="field">
        <span class="field-label">Model</span>
        <select v-model="cfg.model" @change="markDirty">
          <option v-for="m in MODELS" :key="m.value" :value="m.value">{{ m.label }}</option>
        </select>
        <input
          type="text"
          v-model="cfg.model"
          placeholder="Or type a custom model name..."
          @input="markDirty"
          style="margin-top: 4px"
        />
      </label>

      <label class="field">
        <span class="field-label">Base URL</span>
        <input
          type="text"
          v-model="cfg.baseUrl"
          placeholder="https://api.minimax.io/anthropic"
          @input="markDirty"
        />
        <span class="hint">Leave empty to use the platform default.</span>
      </label>

      <label class="field">
        <span class="field-label">API Key</span>
        <div class="key-row">
          <input
            :type="showKey ? 'text' : 'password'"
            v-model="cfg.apiKey"
            placeholder="sk-..."
            @input="markDirty"
          />
          <button type="button" class="eye" @click="showKey = !showKey">
            {{ showKey ? "hide" : "show" }}
          </button>
        </div>
        <span class="hint" v-if="cfg.serverHasApiKey && !cfg.apiKey">
          Server already has a key (from environment).
        </span>
      </label>

      <label class="toggle">
        <input type="checkbox" v-model="cfg.clipboardEnabled" @change="markDirty" />
        <span>Allow the camel to read the clipboard (<code>get_clipboard</code>).</span>
      </label>

      <label class="toggle">
        <input type="checkbox" v-model="cfg.nudgesEnabled" @change="markDirty" />
        <span>Proactive nudges when idle &gt; 30 min.</span>
      </label>

      <label class="toggle">
        <input type="checkbox" v-model="cfg.screenMonitorEnabled" @change="markDirty" />
        <span>Screen activity monitor (captures &amp; classifies your activity).</span>
      </label>

      <label class="field" v-if="cfg.screenMonitorEnabled">
        <span class="field-label">Monitor Interval (seconds)</span>
        <input
          type="number"
          min="10"
          max="3600"
          v-model.number="cfg.monitorIntervalSeconds"
          @input="markDirty"
        />
        <span class="hint">How often to capture and analyze your screen (10–3600s).</span>
      </label>

      <div class="section-divider" />

      <label class="toggle">
        <input type="checkbox" v-model="cfg.focusCoachEnabled" @change="markDirty" />
        <span>
          <strong>Focus Coach</strong> — proactively nudge when distracted too long.
        </span>
      </label>

      <template v-if="cfg.focusCoachEnabled">
        <span class="hint">
          Needs the screen monitor above to be on so there's activity data to read.
        </span>

        <label class="field">
          <span class="field-label">Check interval (seconds)</span>
          <input
            type="number"
            min="30"
            max="3600"
            v-model.number="cfg.focusCoachIntervalSeconds"
            @input="markDirty"
          />
          <span class="hint">How often the coach evaluates recent activity.</span>
        </label>

        <label class="field">
          <span class="field-label">Analysis window (minutes)</span>
          <input
            type="number"
            min="1"
            max="240"
            v-model.number="cfg.focusCoachWindowMinutes"
            @input="markDirty"
          />
          <span class="hint">Look back this far each check.</span>
        </label>

        <label class="field">
          <span class="field-label">Distraction threshold (minutes)</span>
          <input
            type="number"
            min="1"
            max="240"
            v-model.number="cfg.distractedThresholdMinutes"
            @input="markDirty"
          />
          <span class="hint">Fire a nudge when distracted minutes in the window reach this.</span>
        </label>

        <label class="field">
          <span class="field-label">Cooldown after nudge (seconds)</span>
          <input
            type="number"
            min="0"
            max="86400"
            v-model.number="cfg.focusCoachCooldownSeconds"
            @input="markDirty"
          />
          <span class="hint">Stay quiet this long after a nudge before checking again.</span>
        </label>

        <div class="field">
          <span class="field-label">Categories ({{ coachSummary }})</span>
          <span class="hint">Click a chip to cycle: ignored → focus → distracted → ignored.</span>
          <div class="chip-row">
            <button
              v-for="cat in ALL_CATEGORIES"
              :key="cat"
              type="button"
              class="chip"
              :class="catState(cat)"
              @click="cycleCategory(cat)"
            >
              {{ cat }}
            </button>
          </div>
        </div>
      </template>
    </div>

    <div class="footer">
      <button class="danger" @click="clearHistory">Wipe memory</button>
      <div class="spacer" />
      <button :disabled="!dirty" @click="apply">{{ dirty ? "Apply" : "Saved" }}</button>
    </div>
  </div>
</template>

<style scoped>
.panel {
  width: 340px;
  max-height: 480px;
  background: rgba(255, 255, 255, 0.98);
  backdrop-filter: blur(10px);
  border-radius: 14px;
  box-shadow: 0 10px 30px rgba(0, 0, 0, 0.25);
  display: flex;
  flex-direction: column;
  overflow: hidden;
  font-size: 14px;
}
.header {
  padding: 8px 12px;
  background: #6b5a46;
  color: #fff;
  font-weight: 600;
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.close {
  border: none;
  background: transparent;
  color: #fff;
  font-size: 20px;
  line-height: 1;
  cursor: pointer;
  padding: 0 4px;
}
.body {
  padding: 14px;
  display: flex;
  flex-direction: column;
  gap: 14px;
  overflow-y: auto;
}
.field {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.field-label {
  font-weight: 600;
  font-size: 12px;
  color: #555;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
select,
input[type="text"],
input[type="password"],
input[type="number"] {
  padding: 6px 8px;
  border: 1px solid #ddd;
  border-radius: 6px;
  font-size: 14px;
  outline: none;
  background: #fff;
}
select:focus,
input:focus {
  border-color: #e9a96a;
}
.key-row {
  display: flex;
  gap: 6px;
}
.key-row input {
  flex: 1;
}
.eye {
  padding: 4px 8px;
  background: #f3ede3;
  border: 1px solid #ddd;
  border-radius: 6px;
  cursor: pointer;
  font-size: 12px;
}
.toggle {
  display: flex;
  gap: 8px;
  align-items: flex-start;
  cursor: pointer;
}
.toggle input {
  margin-top: 2px;
}
.toggle code {
  background: #f3ede3;
  padding: 1px 4px;
  border-radius: 4px;
  font-size: 12px;
}
.hint {
  font-size: 12px;
  color: #888;
  font-style: italic;
}
.section-divider {
  height: 1px;
  background: #eee;
  margin: 2px 0;
}
.chip-row {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 4px;
}
.chip {
  padding: 4px 10px;
  border-radius: 14px;
  border: 1px solid transparent;
  cursor: pointer;
  font-size: 12px;
  user-select: none;
  transition: background 120ms ease, border-color 120ms ease;
}
.chip.neutral {
  background: #f0eee9;
  color: #888;
  border-color: #e2ddd2;
}
.chip.focus {
  background: #e4f4e1;
  color: #2e6b2a;
  border-color: #b3d9a9;
}
.chip.distracted {
  background: #fbe4e1;
  color: #9a2e22;
  border-color: #e8b5ad;
}
.footer {
  padding: 10px 12px;
  border-top: 1px solid #eee;
  display: flex;
  gap: 8px;
  align-items: center;
}
.spacer {
  flex: 1;
}
.footer button {
  padding: 6px 12px;
  background: #e9a96a;
  color: #fff;
  border: none;
  border-radius: 6px;
  cursor: pointer;
  font-weight: 600;
}
.footer button:disabled {
  background: #ccc;
  cursor: not-allowed;
}
.footer .danger {
  background: #c0392b;
}
</style>
