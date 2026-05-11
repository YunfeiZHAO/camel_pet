<script setup lang="ts">
import { ref, onMounted, computed } from "vue";

const emit = defineEmits<{
  (e: "close"): void;
}>();

type DaySummary = {
  date: string;
  breakdown: Record<string, number>;
  screen_time_minutes: number;
};

const loading = ref(true);
const error = ref("");
const today = ref<DaySummary | null>(null);

const BASE_URL = "http://127.0.0.1:8765";

const CATEGORY_COLORS: Record<string, string> = {
  coding: "#3498db",
  working: "#2ecc71",
  browsing: "#f39c12",
  reading: "#8e44ad",
  design: "#e91e63",
  video: "#e74c3c",
  gaming: "#9b59b6",
  communication: "#1abc9c",
  meeting: "#00bcd4",
  social_media: "#ff7043",
  music: "#ab47bc",
  learning: "#66bb6a",
  idle: "#95a5a6",
  other: "#7f8c8d",
};

async function fetchToday() {
  loading.value = true;
  error.value = "";
  try {
    const resp = await fetch(`${BASE_URL}/activity/today`);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    today.value = await resp.json();
  } catch (e: any) {
    error.value = e.message || "Failed to fetch activity data";
  } finally {
    loading.value = false;
  }
}

const categories = computed(() => {
  if (!today.value?.breakdown) return [];
  return Object.entries(today.value.breakdown)
    .sort((a, b) => b[1] - a[1])
    .map(([status, minutes]) => ({
      status,
      minutes: Math.round(minutes),
      percent: today.value!.screen_time_minutes > 0
        ? Math.round((minutes / today.value!.screen_time_minutes) * 100)
        : 0,
      color: CATEGORY_COLORS[status] || "#7f8c8d",
    }));
});

const screenTimeHours = computed(() => {
  if (!today.value) return "0h 0m";
  const h = Math.floor(today.value.screen_time_minutes / 60);
  const m = Math.round(today.value.screen_time_minutes % 60);
  return h > 0 ? `${h}h ${m}m` : `${m}m`;
});

onMounted(fetchToday);

const capturing = ref(false);
const captureResult = ref<{ status: string; app: string | null } | null>(null);

async function captureNow() {
  capturing.value = true;
  captureResult.value = null;
  try {
    const resp = await fetch(`${BASE_URL}/activity/capture`, { method: "POST" });
    const data = await resp.json();
    if (data.error) {
      error.value = data.error;
    } else {
      captureResult.value = data;
      await fetchToday(); // refresh summary
    }
  } catch (e: any) {
    error.value = e.message || "Capture failed";
  } finally {
    capturing.value = false;
  }
}
</script>

<template>
  <div class="panel" @click.stop>
    <div class="header">
      <span>Activity Dashboard</span>
      <button class="close" @click="emit('close')">×</button>
    </div>
    <div class="body">
      <div v-if="loading" class="center">Loading...</div>
      <div v-else-if="error" class="center error">{{ error }}</div>
      <div v-else-if="!today || categories.length === 0" class="center muted">
        No activity data yet. Enable the screen monitor in Settings.
      </div>
      <template v-else>
        <div class="screen-time">
          <span class="label">Screen Time Today</span>
          <span class="value">{{ screenTimeHours }}</span>
        </div>

        <div class="breakdown">
          <div class="bar-chart">
            <div
              v-for="cat in categories"
              :key="cat.status"
              class="bar-segment"
              :style="{ width: cat.percent + '%', background: cat.color }"
              :title="`${cat.status}: ${cat.minutes}m (${cat.percent}%)`"
            />
          </div>
          <div class="legend">
            <div v-for="cat in categories" :key="cat.status" class="legend-item">
              <span class="dot" :style="{ background: cat.color }" />
              <span class="cat-name">{{ cat.status }}</span>
              <span class="cat-time">{{ cat.minutes }}m</span>
              <span class="cat-pct">({{ cat.percent }}%)</span>
            </div>
          </div>
        </div>
      </template>
    </div>
    <div class="footer">
      <button class="capture-btn" :disabled="capturing" @click="captureNow">
        {{ capturing ? "Capturing..." : "📸 Capture Now" }}
      </button>
      <div class="spacer" />
      <button @click="fetchToday">Refresh</button>
    </div>
    <div v-if="captureResult" class="capture-result">
      Last capture: <strong>{{ captureResult.status }}</strong>
      <span v-if="captureResult.app"> — {{ captureResult.app }}</span>
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
  background: #3498db;
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
  gap: 16px;
  overflow-y: auto;
}
.center {
  text-align: center;
  padding: 24px 0;
  color: #666;
}
.error {
  color: #c0392b;
}
.muted {
  color: #999;
}
.screen-time {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  padding: 12px;
  background: #f8f9fa;
  border-radius: 8px;
}
.screen-time .label {
  font-size: 12px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: #555;
}
.screen-time .value {
  font-size: 24px;
  font-weight: 700;
  color: #2c3e50;
}
.bar-chart {
  display: flex;
  height: 20px;
  border-radius: 10px;
  overflow: hidden;
  background: #eee;
}
.bar-segment {
  min-width: 2px;
  transition: width 0.3s;
}
.legend {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-top: 8px;
}
.legend-item {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
}
.dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  flex-shrink: 0;
}
.cat-name {
  flex: 1;
  text-transform: capitalize;
}
.cat-time {
  font-weight: 600;
  color: #333;
}
.cat-pct {
  color: #888;
  font-size: 12px;
}
.footer {
  padding: 10px 12px;
  border-top: 1px solid #eee;
  display: flex;
  align-items: center;
  gap: 8px;
}
.spacer {
  flex: 1;
}
.footer button {
  padding: 6px 12px;
  background: #3498db;
  color: #fff;
  border: none;
  border-radius: 6px;
  cursor: pointer;
  font-weight: 600;
}
.footer button:hover {
  background: #2980b9;
}
.footer button:disabled {
  background: #bdc3c7;
  cursor: not-allowed;
}
.capture-btn {
  background: #27ae60 !important;
}
.capture-btn:hover:not(:disabled) {
  background: #219a52 !important;
}
.capture-result {
  padding: 6px 12px;
  font-size: 12px;
  color: #555;
  border-top: 1px solid #eee;
  text-align: center;
}
</style>
