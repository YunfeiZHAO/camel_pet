<script setup lang="ts">
import { nextTick, onMounted, ref } from "vue";

const props = defineProps<{ connected: boolean; streaming: boolean }>();
const emit = defineEmits<{
  (e: "send", text: string): void;
  (e: "close"): void;
}>();

const input = ref("");
const inputEl = ref<HTMLInputElement | null>(null);

function send() {
  const t = input.value.trim();
  if (!t || !props.connected || props.streaming) return;
  emit("send", t);
  input.value = "";
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === "Escape") {
    e.preventDefault();
    emit("close");
  }
}

onMounted(async () => {
  await nextTick();
  inputEl.value?.focus();
});
</script>

<template>
  <!-- SVG filter for the torn paper edges — applied to the paper layer only,
       so the text inside stays crisp. -->
  <svg class="defs" width="0" height="0" aria-hidden="true">
    <defs>
      <filter id="parchment-torn" x="-4%" y="-8%" width="108%" height="116%">
        <feTurbulence type="fractalNoise" baseFrequency="0.018 0.03" numOctaves="2" seed="7" />
        <feDisplacementMap in="SourceGraphic" scale="7" />
      </filter>
    </defs>
  </svg>

  <div class="paper-wrap" @click.stop>
    <div class="parchment">
      <div class="paper-bg" />
      <input
        ref="inputEl"
        v-model="input"
        :placeholder="connected ? 'whisper to the camel…' : 'connecting…'"
        :disabled="!connected || streaming"
        @keydown.enter.prevent="send"
        @keydown="onKeydown"
      />
      <button
        class="quill"
        :disabled="!connected || streaming || !input.trim()"
        :title="connected ? 'send' : 'connecting…'"
        @click="send"
      >
        ✒
      </button>
    </div>
    <div class="hint">enter to send · esc to close</div>
  </div>
</template>

<style scoped>
.paper-wrap {
  display: flex;
  flex-direction: column;
  align-items: center;
  pointer-events: auto;
  animation: paper-in 320ms cubic-bezier(0.22, 1.2, 0.36, 1) both;
}

.defs {
  position: absolute;
  width: 0;
  height: 0;
}

/* Parchment container — text layer sits on top, filtered paper shape behind. */
.parchment {
  position: relative;
  width: 320px;
  padding: 22px 30px 26px;
  color: #3a2a18;
  font-family: "Georgia", "Palatino Linotype", "Times New Roman", serif;
  font-style: italic;
  font-size: 15px;
  filter: drop-shadow(0 4px 0 rgba(107, 90, 70, 0.22));
}

/* The aged paper silhouette — layered gradients for stains + a turbulence
   displacement filter to give the edges a ragged, torn look. */
.paper-bg {
  position: absolute;
  inset: 0;
  z-index: 0;
  background:
    radial-gradient(circle at 22% 28%, rgba(120, 80, 30, 0.12) 0 3px, transparent 4px),
    radial-gradient(circle at 78% 72%, rgba(120, 80, 30, 0.10) 0 3px, transparent 4px),
    radial-gradient(circle at 55% 45%, rgba(120, 80, 30, 0.08) 0 2px, transparent 3px),
    radial-gradient(ellipse at top left,    rgba(107, 70, 30, 0.00) 50%, rgba(107, 70, 30, 0.22) 100%),
    radial-gradient(ellipse at bottom right, rgba(107, 70, 30, 0.00) 50%, rgba(107, 70, 30, 0.26) 100%),
    linear-gradient(140deg, #f3dcaa 0%, #e8c88a 55%, #d7ac68 100%);
  border-radius: 14px 22px 16px 20px / 18px 14px 22px 16px;
  box-shadow:
    inset 0 0 40px rgba(120, 70, 20, 0.22),
    inset 0 0 90px rgba(120, 70, 20, 0.08);
  filter: url(#parchment-torn);
}

/* Input takes the full paper — transparent, no border, feels like writing on the page. */
input {
  position: relative;
  z-index: 1;
  display: block;
  width: 100%;
  background: transparent;
  border: none;
  outline: none;
  color: #3a2a18;
  font-family: inherit;
  font-style: italic;
  font-size: 16px;
  line-height: 1.5;
  padding: 0 28px 0 0; /* room for the quill on the right */
}
input::placeholder {
  color: rgba(107, 70, 30, 0.5);
  font-style: italic;
}
input::selection {
  background: rgba(107, 70, 30, 0.25);
}

/* Tiny quill send button in the bottom-right corner of the paper. */
.quill {
  position: absolute;
  bottom: 6px;
  right: 12px;
  z-index: 1;
  background: transparent;
  border: none;
  color: #6b5a46;
  opacity: 0.65;
  cursor: pointer;
  font-size: 20px;
  line-height: 1;
  padding: 4px;
  transition: opacity 150ms ease, transform 150ms ease;
}
.quill:hover:not(:disabled) {
  opacity: 1;
  transform: translateY(-1px) rotate(-8deg);
}
.quill:disabled {
  opacity: 0.25;
  cursor: not-allowed;
}

.hint {
  margin-top: 8px;
  font-size: 11px;
  font-style: italic;
  color: rgba(107, 90, 70, 0.65);
  font-family: "Georgia", serif;
}

@keyframes paper-in {
  0%   { opacity: 0; transform: translateY(-8px) rotate(-2deg) scale(0.92); }
  60%  { opacity: 1; transform: translateY(1px) rotate(0.5deg) scale(1.02); }
  100% { opacity: 1; transform: translateY(0) rotate(0deg) scale(1); }
}
</style>
