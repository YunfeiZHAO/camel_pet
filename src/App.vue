<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from "vue";
import { invoke } from "@tauri-apps/api/core";
import { getCurrentWindow, PhysicalPosition, PhysicalSize, primaryMonitor } from "@tauri-apps/api/window";
import PetCanvas from "./components/PetCanvas.vue";
import SettingsPanel from "./components/SettingsPanel.vue";
import ActivityDashboard from "./components/ActivityDashboard.vue";
import ScrollInput from "./components/ScrollInput.vue";
import { AgentSocket, type AgentEvent } from "./services/agentSocket";
import { useConfigStore } from "./stores/config";

const cfg = useConfigStore();
const socket = new AgentSocket();
const connected = ref(false);

const settingsOpen = ref(false);
const activityOpen = ref(false);
const scrollOpen = ref(false);

const streaming = ref(false);
const mood = ref<"idle" | "talking" | "happy">("idle");

let happyTimer: number | null = null;

const randomSentences = [
  "Today is full of hope!",
  "Are you watching me?",
  "Spacing out…",
  "Time for a break?",
  "I'm right here with you.",
];

// --- camel position + walking ---
const PET_W = 140;
const PET_H = 140;
const BUBBLE_SLOT_H = 120;

const viewportW = ref(typeof window !== "undefined" ? window.innerWidth : 1200);
const viewportH = ref(typeof window !== "undefined" ? window.innerHeight : 800);

// Position is the top-left of the camel (inside the bubble+pet column cluster).
const camelPos = ref({ x: 400, y: 400 });
const isWalking = ref(false);
let walkTransitionTimer: number | null = null;

function clampCamel(x: number, y: number) {
  // Keep the camel glyph fully inside the viewport. Bubbles and panels are
  // allowed to overflow the screen edges (panels scroll internally; a bubble
  // too close to an edge just clips).
  const minX = PET_W / 2;
  const maxX = Math.max(minX, viewportW.value - PET_W / 2);
  const minY = 10;
  const maxY = Math.max(minY, viewportH.value - PET_H - 10);
  return {
    x: Math.min(Math.max(x, minX), maxX),
    y: Math.min(Math.max(y, minY), maxY),
  };
}

const WALK_MS = 1600;
const WALK_MIN_DIST = 260;
const WALK_MAX_DIST = 420;

function walkToNearby(onDone?: () => void) {
  let best = camelPos.value;
  let bestScore = -Infinity;
  for (let attempt = 0; attempt < 8; attempt++) {
    const angle = Math.random() * Math.PI * 2;
    const dist = WALK_MIN_DIST + Math.random() * (WALK_MAX_DIST - WALK_MIN_DIST);
    const t = clampCamel(
      camelPos.value.x + Math.cos(angle) * dist,
      camelPos.value.y + Math.sin(angle) * dist,
    );
    // Score: prefer far from current position AND far from nearest dropped bubble.
    const moveDist = Math.hypot(t.x - camelPos.value.x, t.y - camelPos.value.y);
    let nearestBubble = Infinity;
    for (const b of droppedBubbles.value) {
      const bCx = b.x + 120; // approx bubble center (max-width 240)
      const bCy = b.y + 40;
      nearestBubble = Math.min(nearestBubble, Math.hypot(t.x - bCx, t.y - bCy));
    }
    const score = moveDist + (nearestBubble === Infinity ? 0 : Math.min(nearestBubble, 300));
    if (score > bestScore) {
      bestScore = score;
      best = t;
    }
  }

  isWalking.value = true;
  mood.value = "talking";
  camelPos.value = best;
  if (walkTransitionTimer) clearTimeout(walkTransitionTimer);
  walkTransitionTimer = window.setTimeout(() => {
    walkTransitionTimer = null;
    isWalking.value = false;
    mood.value = "idle";
    if (onDone) onDone();
  }, WALK_MS);
}

function onDragStart() {
  // Cancel any walk in progress so the user has full control.
  if (isWalking.value) {
    isWalking.value = false;
    mood.value = "idle";
    if (walkTransitionTimer) {
      clearTimeout(walkTransitionTimer);
      walkTransitionTimer = null;
    }
  }
}

function onDragMove(dx: number, dy: number) {
  const next = clampCamel(camelPos.value.x + dx, camelPos.value.y + dy);
  camelPos.value = next;
}

// --- bubble / reply state ---
const bubbleText = ref("");
const showBubble = ref(false);
const bubbleKind = ref<"idle" | "reply" | "system">("idle");
const bubbleKey = ref(0);
const thinking = ref(false);
let randomSpeakInterval: number | null = null;
let bubbleHideTimeout: number | null = null;

// Buffer-then-chunk reply pipeline. We wait for the full LLM response, then
// split it into readable chunks and play them one at a time — the camel walks
// a step between chunks, so the dropped bubbles don't overlap.
const CHUNK_MIN = 40;
const CHUNK_TARGET = 75;
const CHUNK_MAX = 130;
const READ_MS_PER_CHAR = 60; // ~16 chars/sec, average reading speed
const READ_MIN_MS = 3500;
const READ_MAX_MS = 9000;

let replyBuffer = "";
let chunkQueue: string[] = [];
let chunkIdx = 0;
let chunkTimer: number | null = null;

// --- dropped bubble trail ---
type DroppedBubble = {
  id: number;
  text: string;
  kind: "reply" | "system";
  x: number; // absolute window position (top-left of bubble)
  y: number;
};
const droppedBubbles = ref<DroppedBubble[]>([]);
let nextBubbleId = 1;
const droppedBubbleEls = ref<Record<number, HTMLElement | null>>({});

function setDroppedBubbleEl(id: number, el: HTMLElement | null) {
  droppedBubbleEls.value[id] = el;
}

function dismissDroppedBubble(id: number) {
  droppedBubbles.value = droppedBubbles.value.filter((b) => b.id !== id);
  delete droppedBubbleEls.value[id];
}

function clearAllDroppedBubbles() {
  droppedBubbles.value = [];
  droppedBubbleEls.value = {};
}

// --- dropped-bubble drag ---
let bubbleDrag: { id: number; offsetX: number; offsetY: number; pointerId: number } | null = null;

function onBubblePointerDown(e: PointerEvent, b: DroppedBubble) {
  if (e.button !== 0) return; // left button only; right-click still dismisses
  e.stopPropagation();
  const el = e.currentTarget as HTMLElement;
  try {
    el.setPointerCapture(e.pointerId);
  } catch {
    // ignore
  }
  bubbleDrag = {
    id: b.id,
    offsetX: e.clientX - b.x,
    offsetY: e.clientY - b.y,
    pointerId: e.pointerId,
  };
  el.classList.add("dragging");
}

function onBubblePointerMove(e: PointerEvent) {
  if (!bubbleDrag || e.pointerId !== bubbleDrag.pointerId) return;
  const b = droppedBubbles.value.find((x) => x.id === bubbleDrag!.id);
  if (!b) return;
  b.x = e.clientX - bubbleDrag.offsetX;
  b.y = e.clientY - bubbleDrag.offsetY;
}

function onBubblePointerUp(e: PointerEvent) {
  if (!bubbleDrag || e.pointerId !== bubbleDrag.pointerId) return;
  try {
    (e.currentTarget as Element).releasePointerCapture(e.pointerId);
  } catch {
    // ignore
  }
  (e.currentTarget as HTMLElement).classList.remove("dragging");
  bubbleDrag = null;
}

function clearChunkTimer() {
  if (chunkTimer !== null) {
    clearTimeout(chunkTimer);
    chunkTimer = null;
  }
}

function resetReplyState() {
  replyBuffer = "";
  chunkQueue = [];
  chunkIdx = 0;
  clearChunkTimer();
}

// Split a complete reply into reasonably sized readable chunks, preferring
// sentence boundaries, then whitespace / comma, then a hard cut.
function chunkReply(text: string): string[] {
  const out: string[] = [];
  let start = 0;
  const len = text.length;
  while (start < len) {
    const remaining = len - start;
    if (remaining <= CHUNK_MAX) {
      const rest = text.slice(start).trim();
      if (rest) out.push(rest);
      break;
    }
    const minEnd = start + CHUNK_MIN;
    const targetEnd = start + CHUNK_TARGET;
    const hardEnd = Math.min(start + CHUNK_MAX, len);

    let breakAt = -1;
    // 1) sentence boundary closest to target
    for (let i = hardEnd - 1; i >= minEnd; i--) {
      const c = text[i];
      if (c === "." || c === "!" || c === "?" || c === "\n" || c === "。" || c === "！" || c === "？") {
        breakAt = i + 1;
        break;
      }
    }
    // 2) whitespace / soft punctuation
    if (breakAt === -1) {
      for (let i = hardEnd - 1; i >= minEnd; i--) {
        const c = text[i];
        if (/\s/.test(c) || c === "," || c === "；" || c === "，" || c === ";") {
          breakAt = i + 1;
          break;
        }
      }
    }
    // 3) hard cut at target
    if (breakAt === -1) breakAt = targetEnd;

    const piece = text.slice(start, breakAt).trim();
    if (piece) out.push(piece);
    start = breakAt;
  }
  return out;
}

function readMsForChunk(text: string) {
  const raw = Math.round(text.length * READ_MS_PER_CHAR);
  return Math.min(READ_MAX_MS, Math.max(READ_MIN_MS, raw));
}

// Show the chunk at chunkIdx, then after its read time drop it and advance
// (walk the camel, show next) or — if this was the last chunk — walk away.
function showCurrentChunk() {
  clearChunkTimer();
  if (chunkIdx >= chunkQueue.length) return;

  const text = chunkQueue[chunkIdx];
  if (bubbleHideTimeout) {
    clearTimeout(bubbleHideTimeout);
    bubbleHideTimeout = null;
  }
  bubbleText.value = text;
  bubbleKind.value = "reply";
  bubbleKey.value += 1;
  showBubble.value = true;

  chunkTimer = window.setTimeout(() => {
    chunkTimer = null;
    dropBubbleAtCurrentPos(text, "reply");
    showBubble.value = false;
    chunkIdx += 1;
    if (chunkIdx < chunkQueue.length) {
      walkToNearby(() => showCurrentChunk());
    } else {
      walkToNearby();
    }
  }, readMsForChunk(text));
}

function finalizeReply() {
  streaming.value = false;
  mood.value = "idle";
  thinking.value = false;

  const full = replyBuffer.trim();
  if (!full) return;
  chunkQueue = chunkReply(full);
  chunkIdx = 0;
  if (chunkQueue.length > 0) {
    showCurrentChunk();
  }
}

// Compute where the live bubble is on screen *right now*, then stash that
// position in the dropped-bubble list. The bubble becomes a static element
// anchored to the desktop while the camel walks away.
function dropBubbleAtCurrentPos(text: string, kind: "reply" | "system") {
  const rect = bubbleEl.value?.getBoundingClientRect();
  // Fallback: just above the camel.
  const x = rect ? rect.left : camelPos.value.x + PET_W / 2 - 120;
  const y = rect ? rect.top : camelPos.value.y - 40;
  droppedBubbles.value.push({
    id: nextBubbleId++,
    text,
    kind,
    x,
    y,
  });
  // Cap the trail so memory doesn't grow unbounded.
  if (droppedBubbles.value.length > 20) {
    droppedBubbles.value.splice(0, droppedBubbles.value.length - 20);
  }
}

// --- refs for passthrough regions ---
const petWrapEl = ref<HTMLDivElement | null>(null);
const bubbleEl = ref<HTMLDivElement | null>(null);
const belowWrapEl = ref<HTMLDivElement | null>(null);
const clearChipEl = ref<HTMLDivElement | null>(null);

function closeAllPanels() {
  settingsOpen.value = false;
  activityOpen.value = false;
  scrollOpen.value = false;
}

function onLeftClick() {
  if (settingsOpen.value || scrollOpen.value) {
    closeAllPanels();
    return;
  }
  if (activityOpen.value) {
    activityOpen.value = false;
    return;
  }
  closeAllPanels();
  activityOpen.value = true;
  flashHappy();
}

function onRightClick() {
  if (activityOpen.value || scrollOpen.value) {
    closeAllPanels();
    settingsOpen.value = true;
    return;
  }
  settingsOpen.value = !settingsOpen.value;
}

function onDoubleClick() {
  closeAllPanels();
  scrollOpen.value = true;
  flashHappy();
}

function flashHappy() {
  mood.value = "happy";
  if (happyTimer) clearTimeout(happyTimer);
  happyTimer = window.setTimeout(() => {
    if (mood.value === "happy") mood.value = "idle";
  }, 1500);
}

function showBubbleMessage(text: string, kind: "idle" | "system", autoHideMs: number | null) {
  resetReplyState();
  bubbleText.value = text;
  bubbleKind.value = kind;
  bubbleKey.value += 1;
  showBubble.value = true;
  if (bubbleHideTimeout) {
    clearTimeout(bubbleHideTimeout);
    bubbleHideTimeout = null;
  }
  if (autoHideMs !== null) {
    bubbleHideTimeout = window.setTimeout(() => {
      showBubble.value = false;
    }, autoHideMs);
  }
}

function onEvent(e: AgentEvent) {
  if (e.type === "ready") {
    connected.value = true;
    cfg.serverHasApiKey = e.has_api_key;
    cfg.serverModel = e.model;
    if (e.init_error) {
      showBubbleMessage(`agent init: ${e.init_error}`, "system", 7000);
    }
    pushPendingConfig();
  } else if (e.type === "config_ack") {
    cfg.serverHasApiKey = e.has_api_key;
    cfg.serverModel = e.model;
  } else if (e.type === "token") {
    // Buffer quietly while the thinking dots show. We don't render tokens
    // live — we wait for `done`, chunk the full reply, and play chunks.
    replyBuffer += e.text;
  } else if (e.type === "done") {
    finalizeReply();
  } else if (e.type === "error") {
    thinking.value = false;
    showBubbleMessage(`⚠️ ${e.message}`, "system", 7000);
    streaming.value = false;
    mood.value = "idle";
  } else if (e.type === "timer_fired") {
    showBubbleMessage(`⏰ ${e.message}`, "system", 9000);
    mood.value = "happy";
  } else if (e.type === "nudge") {
    showBubbleMessage("the camel looks at you from across the dunes", "system", 7000);
  } else if (e.type === "proactive") {
    // Camel initiates: reuse the chat-reply chunk pipeline so the bubbles
    // drop to the desktop trail just like a normal reply.
    resetReplyState();
    if (bubbleHideTimeout) {
      clearTimeout(bubbleHideTimeout);
      bubbleHideTimeout = null;
    }
    showBubble.value = false;
    thinking.value = false;
    streaming.value = false;
    replyBuffer = e.text;
    finalizeReply();
  } else if (e.type === "history_cleared") {
    showBubbleMessage("memory wiped.", "system", 3000);
  }
}

function pushPendingConfig() {
  socket.sendConfig({
    model: cfg.model,
    api_key: cfg.apiKey || undefined,
    platform: cfg.platform || undefined,
    base_url: cfg.baseUrl || undefined,
    clipboard_enabled: cfg.clipboardEnabled,
    nudges_enabled: cfg.nudgesEnabled,
    screen_monitor_enabled: cfg.screenMonitorEnabled,
    monitor_interval_seconds: cfg.monitorIntervalSeconds,
    focus_coach_enabled: cfg.focusCoachEnabled,
    focus_coach_interval_seconds: cfg.focusCoachIntervalSeconds,
    focus_coach_window_minutes: cfg.focusCoachWindowMinutes,
    distracted_threshold_minutes: cfg.distractedThresholdMinutes,
    focus_coach_cooldown_seconds: cfg.focusCoachCooldownSeconds,
    focus_categories: [...cfg.focusCategories],
    distraction_categories: [...cfg.distractionCategories],
  });
}

function onSend(text: string) {
  if (!socket.send(text)) return;
  streaming.value = true;
  mood.value = "talking";
  resetReplyState();
  if (bubbleKind.value === "reply") {
    showBubble.value = false;
  }
  if (bubbleHideTimeout) {
    clearTimeout(bubbleHideTimeout);
    bubbleHideTimeout = null;
  }
  thinking.value = true;
}

function onApplySettings() {
  pushPendingConfig();
}

function onClearHistory() {
  socket.clearHistory();
}

const anyPanelOpen = computed(
  () => settingsOpen.value || activityOpen.value || scrollOpen.value,
);

watch(anyPanelOpen, (open) => {
  if (open && bubbleKind.value === "idle") {
    showBubble.value = false;
  }
});

// Extra padding around the camel's visible bounding box. The camel sprite is
// animated (breathe, walk, happy), so the measured rect fluctuates slightly —
// the padding gives the user a forgiving circular hit region.
const CAMEL_HIT_PADDING_PX = 18;

async function syncPassthroughRegions() {
  await nextTick();
  const rects: [number, number, number, number][] = [];
  const circles: [number, number, number][] = [];

  const pushRect = (el: HTMLElement | null | undefined) => {
    if (!el) return;
    const r = el.getBoundingClientRect();
    if (r.width > 0 && r.height > 0) {
      rects.push([r.left, r.top, r.width, r.height]);
    }
  };

  // Camel uses a circle so the hit area matches the cartoon sprite (and stays
  // generous at the corners). The rect follows the CSS transform on the cluster,
  // so measuring every frame during a walk keeps the circle in sync with the
  // visually animated position — no "click falls through mid-walk" gap.
  const petEl = petWrapEl.value;
  if (petEl) {
    const r = petEl.getBoundingClientRect();
    if (r.width > 0 && r.height > 0) {
      const cx = r.left + r.width / 2;
      const cy = r.top + r.height / 2;
      const radius = Math.max(r.width, r.height) / 2 + CAMEL_HIT_PADDING_PX;
      circles.push([cx, cy, radius]);
    }
  }

  if (showBubble.value || thinking.value) pushRect(bubbleEl.value);
  if (anyPanelOpen.value) pushRect(belowWrapEl.value);
  for (const b of droppedBubbles.value) {
    pushRect(droppedBubbleEls.value[b.id]);
  }
  if (droppedBubbles.value.length >= 2) pushRect(clearChipEl.value);

  try {
    await Promise.all([
      invoke("set_passthrough_regions", { rects }),
      invoke("set_passthrough_circles", { circles }),
    ]);
  } catch (err) {
    console.warn("set_passthrough regions/circles failed", err);
  }
}

let resyncTimer: number | null = null;
function scheduleResync() {
  if (resyncTimer !== null) return;
  resyncTimer = window.setTimeout(() => {
    resyncTimer = null;
    void syncPassthroughRegions();
  }, 50);
}

// During a walk, the cluster animates via a 1.5 s CSS transition. Regular
// sync (50 ms debounce + 500 ms interval) is too coarse — the camel's hit
// circle would lag the sprite. Run a rAF loop for the duration of each walk.
let walkRafId: number | null = null;
function startWalkSync() {
  if (walkRafId !== null) return;
  const step = () => {
    void syncPassthroughRegions();
    if (isWalking.value) {
      walkRafId = requestAnimationFrame(step);
    } else {
      walkRafId = null;
    }
  };
  walkRafId = requestAnimationFrame(step);
}
function stopWalkSync() {
  if (walkRafId !== null) {
    cancelAnimationFrame(walkRafId);
    walkRafId = null;
  }
}

watch(isWalking, (walking) => {
  if (walking) startWalkSync();
  else stopWalkSync();
});

watch(
  [anyPanelOpen, showBubble, thinking, settingsOpen, activityOpen, scrollOpen, bubbleKey, camelPos, droppedBubbles],
  () => scheduleResync(),
  { deep: true },
);

function handleOutsideClick(e: MouseEvent) {
  if (!scrollOpen.value) return;
  const target = e.target as Node | null;
  if (petWrapEl.value?.contains(target)) return;
  if (belowWrapEl.value?.contains(target)) return;
  scrollOpen.value = false;
}

// The Rust setup hook sizes the window to the primary monitor before the
// webview shows. This is a belt-and-suspenders re-apply from JS in case a
// Windows / DPI edge-case silently ignored the initial setSize.
async function measureViewport() {
  try {
    const win = getCurrentWindow();
    const mon = await primaryMonitor();
    if (mon) {
      const expectedW = mon.size.width;
      const expectedH = mon.size.height;
      // Measure current outer size in physical pixels.
      const outer = await win.outerSize();
      if (outer.width < expectedW - 2 || outer.height < expectedH - 2) {
        console.info(`window ${outer.width}x${outer.height} < monitor ${expectedW}x${expectedH} — resizing from JS`);
        try {
          if (await win.isMaximized()) await win.unmaximize();
        } catch {
          // ignore
        }
        await win.setPosition(new PhysicalPosition(mon.position.x, mon.position.y));
        await win.setSize(new PhysicalSize(expectedW, expectedH));
        await new Promise((r) => setTimeout(r, 50));
      }
    }
  } catch (err) {
    console.warn("measureViewport monitor check failed", err);
  }

  await nextTick();
  viewportW.value = window.innerWidth;
  viewportH.value = window.innerHeight;
  camelPos.value = clampCamel(viewportW.value / 2, viewportH.value / 2 - PET_H / 2);
  console.info(`camel viewport ${viewportW.value}x${viewportH.value}`);
}

function onResize() {
  viewportW.value = window.innerWidth;
  viewportH.value = window.innerHeight;
  camelPos.value = clampCamel(camelPos.value.x, camelPos.value.y);
  scheduleResync();
}

onMounted(async () => {
  await measureViewport();

  socket.on(onEvent);
  socket.connect();
  const iv = setInterval(() => {
    connected.value = socket.ready;
  }, 500);
  window.addEventListener("beforeunload", () => clearInterval(iv));

  randomSpeakInterval = window.setInterval(() => {
    if (scrollOpen.value || streaming.value) return;
    if (bubbleKind.value === "reply" || bubbleKind.value === "system") return;
    if (Math.random() < 0.9) {
      showBubbleMessage(
        randomSentences[Math.floor(Math.random() * randomSentences.length)],
        "idle",
        4500,
      );
    }
  }, 10000);

  document.addEventListener("mousedown", handleOutsideClick);
  window.addEventListener("resize", onResize);

  void syncPassthroughRegions();
  const rectsIv = window.setInterval(syncPassthroughRegions, 500);
  window.addEventListener("beforeunload", () => {
    clearInterval(rectsIv);
  });
});

onUnmounted(() => {
  if (randomSpeakInterval) clearInterval(randomSpeakInterval);
  if (bubbleHideTimeout) clearTimeout(bubbleHideTimeout);
  if (walkTransitionTimer) clearTimeout(walkTransitionTimer);
  clearChunkTimer();
  document.removeEventListener("mousedown", handleOutsideClick);
  window.removeEventListener("resize", onResize);
});

// Cluster position — camelPos points at the pet; bubble sits above, panel sits below.
const clusterStyle = computed(() => ({
  transform: `translate3d(${camelPos.value.x}px, ${camelPos.value.y - BUBBLE_SLOT_H}px, 0)`,
  transition: isWalking.value ? "transform 1500ms cubic-bezier(0.4, 0.05, 0.4, 1)" : "none",
}));
</script>

<template>
  <div class="overlay">
    <!-- Dropped conversation trail: static bubbles anchored to desktop positions. -->
    <div
      v-for="b in droppedBubbles"
      :key="b.id"
      :ref="(el) => setDroppedBubbleEl(b.id, el as HTMLElement | null)"
      class="dropped"
      :style="{ transform: `translate3d(${b.x}px, ${b.y}px, 0)` }"
      @pointerdown="onBubblePointerDown($event, b)"
      @pointermove="onBubblePointerMove"
      @pointerup="onBubblePointerUp"
      @pointercancel="onBubblePointerUp"
      @contextmenu.prevent="dismissDroppedBubble(b.id)"
      :title="'drag to move · right-click to dismiss'"
    >
      <div class="pet-bubble" :class="b.kind">
        <span class="bubble-text">{{ b.text }}</span>
        <span class="tail" />
      </div>
    </div>

    <!-- The moving cluster: bubble slot + camel + optional panel. -->
    <div class="cluster" :style="clusterStyle">
      <div class="bubble-slot">
        <Transition name="pop" mode="out-in">
          <div
            v-if="showBubble"
            :key="bubbleKey"
            ref="bubbleEl"
            class="pet-bubble"
            :class="bubbleKind"
          >
            <span class="bubble-text">{{ bubbleText }}</span>
            <span class="tail" />
          </div>
          <div v-else-if="thinking" ref="bubbleEl" class="thinking" aria-label="thinking">
            <span class="dot" />
            <span class="dot" />
            <span class="dot" />
          </div>
        </Transition>
      </div>

      <div ref="petWrapEl" class="pet-wrap">
        <PetCanvas
          :mood="mood"
          :connected="connected"
          @left-click="onLeftClick"
          @right-click="onRightClick"
          @double-click="onDoubleClick"
          @drag-start="onDragStart"
          @drag-move="onDragMove"
        />
      </div>

      <div v-if="anyPanelOpen" ref="belowWrapEl" class="below-wrap">
        <ActivityDashboard
          v-if="activityOpen"
          @close="activityOpen = false"
        />
        <SettingsPanel
          v-else-if="settingsOpen"
          @close="settingsOpen = false"
          @apply="onApplySettings"
          @clear-history="onClearHistory"
        />
        <ScrollInput
          v-else-if="scrollOpen"
          :connected="connected"
          :streaming="streaming"
          @send="onSend"
          @close="scrollOpen = false"
        />
      </div>
    </div>

    <!-- Clear-all trail chip, only when there are at least 2 dropped bubbles. -->
    <div
      v-if="droppedBubbles.length >= 2"
      ref="clearChipEl"
      class="clear-all"
      @click="clearAllDroppedBubbles"
      @contextmenu.prevent="clearAllDroppedBubbles"
      :title="'clear all dropped bubbles'"
    >
      clear ×{{ droppedBubbles.length }}
    </div>
  </div>
</template>

<style scoped>
.overlay {
  width: 100vw;
  height: 100vh;
  position: relative;
  background: transparent;
  overflow: hidden;
}

.cluster {
  position: absolute;
  top: 0;
  left: 0;
  width: 0;
  height: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  will-change: transform;
  pointer-events: none;
}

.cluster > * {
  pointer-events: auto;
}

.bubble-slot {
  flex: 0 0 auto;
  min-height: 120px;
  display: flex;
  align-items: flex-end;
  justify-content: center;
  width: 320px;
  margin-bottom: 4px;
  pointer-events: none;
}

.pet-wrap {
  flex: 0 0 auto;
  position: relative;
}

.below-wrap {
  flex: 0 0 auto;
  margin-top: 10px;
  display: flex;
  justify-content: center;
}

/* --- dropped bubble trail --- */
.dropped {
  position: absolute;
  top: 0;
  left: 0;
  pointer-events: auto;
  cursor: grab;
  touch-action: none;
}
.dropped.dragging {
  cursor: grabbing;
  z-index: 20;
}
.dropped .pet-bubble {
  opacity: 0.92;
  filter: drop-shadow(0 4px 0 rgba(107, 90, 70, 0.22));
}
.dropped:hover .pet-bubble {
  opacity: 1;
  outline: 2px dashed rgba(107, 90, 70, 0.45);
  outline-offset: 4px;
}
.dropped.dragging .pet-bubble {
  opacity: 1;
  transform: rotate(-1.5deg);
  outline: 2px solid rgba(107, 90, 70, 0.55);
  outline-offset: 4px;
}

/* --- bubble + thinking --- */
.pet-bubble {
  position: relative;
  padding: 12px 18px;
  background: #fffdf7;
  color: #3a2a18;
  border: 2px solid #6b5a46;
  border-radius: 28px 30px 26px 32px / 30px 26px 32px 28px;
  font-size: 14px;
  line-height: 1.5;
  max-width: 240px;
  white-space: pre-wrap;
  word-wrap: break-word;
  pointer-events: auto;
  z-index: 10;
  filter: drop-shadow(0 4px 0 rgba(107, 90, 70, 0.18));
}

.pet-bubble::before {
  content: "";
  position: absolute;
  top: -9px;
  left: 18px;
  width: 20px;
  height: 20px;
  background: #fffdf7;
  border: 2px solid #6b5a46;
  border-radius: 50%;
  border-bottom-color: transparent;
  border-right-color: transparent;
  transform: rotate(35deg);
}
.pet-bubble::after {
  content: "";
  position: absolute;
  top: -7px;
  right: 22px;
  width: 14px;
  height: 14px;
  background: #fffdf7;
  border: 2px solid #6b5a46;
  border-radius: 50%;
  border-bottom-color: transparent;
  border-left-color: transparent;
  transform: rotate(-40deg);
}

.pet-bubble .tail,
.pet-bubble .tail::before {
  position: absolute;
  background: #fffdf7;
  border: 2px solid #6b5a46;
  border-radius: 50%;
}
.pet-bubble .tail {
  width: 10px;
  height: 10px;
  bottom: -14px;
  left: 50%;
  transform: translateX(-18px);
}
.pet-bubble .tail::before {
  content: "";
  width: 6px;
  height: 6px;
  bottom: -12px;
  left: 10px;
}

.pet-bubble.idle {
  white-space: nowrap;
  max-width: 280px;
}

.pet-bubble.system {
  background-color: #f6ecd6;
  color: #6b5a46;
  font-style: italic;
  border-color: #6b5a46;
}
.pet-bubble.system::before,
.pet-bubble.system::after,
.pet-bubble.system .tail,
.pet-bubble.system .tail::before {
  background-color: #f6ecd6;
}

.cursor {
  display: inline-block;
  margin-left: 2px;
  animation: blink 1s steps(2) infinite;
}

.pop-enter-active {
  animation: cloud-in 260ms cubic-bezier(0.34, 1.56, 0.64, 1) both;
}
.pop-leave-active {
  animation: cloud-out 180ms ease-in both;
}

.thinking {
  display: flex;
  gap: 6px;
  padding: 10px 14px;
  background: #fffdf7;
  border: 2px solid #6b5a46;
  border-radius: 20px;
  filter: drop-shadow(0 3px 0 rgba(107, 90, 70, 0.18));
  pointer-events: auto;
}
.thinking .dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #6b5a46;
  animation: think-bounce 1.1s ease-in-out infinite;
}
.thinking .dot:nth-child(2) { animation-delay: 0.18s; }
.thinking .dot:nth-child(3) { animation-delay: 0.36s; }

/* --- clear-all chip --- */
.clear-all {
  position: absolute;
  top: 14px;
  right: 18px;
  padding: 6px 12px;
  background: #fffdf7;
  color: #6b5a46;
  border: 2px solid #6b5a46;
  border-radius: 16px;
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  pointer-events: auto;
  user-select: none;
  filter: drop-shadow(0 3px 0 rgba(107, 90, 70, 0.18));
  transition: transform 150ms ease, background 150ms ease;
}
.clear-all:hover {
  background: #f6ecd6;
  transform: translateY(-1px);
}

@keyframes blink {
  50% { opacity: 0; }
}
@keyframes cloud-in {
  0%   { opacity: 0; transform: scale(0.6) translateY(6px); }
  60%  { opacity: 1; transform: scale(1.04) translateY(-1px); }
  100% { opacity: 1; transform: scale(1) translateY(0); }
}
@keyframes cloud-out {
  0%   { opacity: 1; transform: scale(1); }
  100% { opacity: 0; transform: scale(0.8) translateY(-4px); }
}
@keyframes think-bounce {
  0%, 60%, 100% { transform: translateY(0); opacity: 0.45; }
  30%           { transform: translateY(-4px); opacity: 1; }
}
</style>

<style>
html, body {
  margin: 0;
  padding: 0;
  overflow: hidden;
  background-color: transparent !important;
}
</style>
