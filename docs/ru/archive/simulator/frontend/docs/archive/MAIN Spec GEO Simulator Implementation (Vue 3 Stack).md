
# GEO Simulator: Visual & Technical Specification

## 1. Technology Stack Implementation Guide

| React Concept (Current) | Vue 3 Equivalent (Target) | Notes |
|-------------------------|---------------------------|-------|
| `useRef(generateData())` | `const nodes = ref([])` | Данные графа должны быть реактивными, но отрисовка в Canvas берет их значения напрямую (unwrapped). |
| `useEffect(render, [])` | `onMounted(() => { loop() })` | Запуск `requestAnimationFrame` строго после монтирования DOM. |
| `ctx.globalCompositeOperation` | Same (Canvas API) | Canvas API идентичен во всех фреймворках. |
| `useState(selectedNode)` | `const selectedNode = ref(null)` | Используйте `computed` для вывода данных в UI карточку. |

## 2. Visual System (Design Tokens)

### Palette
*   **Background:** `#020408` (Deep Space)
*   **Business Nodes:** `#10b981` (Emerald-500)
*   **Person Nodes:** `#3b82f6` (Blue-500)
*   **Active Link:** Gradient `Blue -> White -> Emerald`
*   **Clearing/Sparks:** `#fbbf24` (Amber-400)
*   **UI Text:** `#ffffff` (Primary), `#94a3b8` (Secondary)

### Rendering Layers (Order is critical)
1.  **Clear Layer:** `clearRect`
2.  **Bokeh Layer:** Большие радиальные градиенты (Alpha 0.1-0.15) на фоне.
3.  **Star Field:** 2-3 слоя параллакса. Дальние звезды — 1px, ближние — 2px с `shadowBlur`.
4.  **Connections (Mesh):**
    *   *Idle:* `lineWidth: 0.5`, `opacity: 0.1`.
    *   *Active:* `lineWidth: 1.5`, `globalCompositeOperation = 'lighter'`.
5.  **Nodes (Glow):**
    *   *Underlay:* Radial Gradient (Bloom) размером 400-600% от размера узла.
    *   *Body:* Fill rect/arc.
6.  **Particles:** Аддитивный блендинг (`lighter`).
7.  **Overlay UI:** Линии-поводки к карточкам (рисуются поверх всего).

## 3. Interaction & Physics Logic

### Topology (Mesh)
*   Узлы не просто висят в пространстве. Каждый узел имеет связи с 2-4 ближайшими соседями (`d3-force` link force).
*   **Focus Interaction:** При клике на узел (`selectedNode`), связи, ведущие *напрямую* к нему, становятся яркими. Остальные связи затухают (`opacity: 0.05`).

### Particle Systems
1.  **Single Transaction:**
    *   **Trigger:** Клик в пустоту или кнопка "Single Tx".
    *   **Visual:** "Комета" (белая голова, цветной хвост).
    *   **Path:** Линейная интерполяция от Source к Target.
2.  **Clearing (Debt Cycle):**
    *   **Trigger:** Кнопка "Clearing".
    *   **Behavior:** Находит треугольники (A->B->C) или пары и запускает встречные потоки.
    *   **Collision:** Когда частицы встречаются в середине связи -> спавн взрыва (`spawnExplosion`).

### Explosion Physics
*   Не просто исчезновение. Создается 15-30 частиц.
*   `vx/vy`: Random angle + Speed.
*   `drag`: 0.95 (трение воздуха, частицы должны тормозить).
*   `life`: 1.0 -> 0.0.
*   **Shockwave:** Расширяющееся пустое кольцо (`ctx.stroke()`).

## 4. Vue 3 Component Structure (Template)

```typescript
<script setup lang="ts">
import { ref, onMounted, onUnmounted, computed } from 'vue';
import { useWindowSize } from '@vueuse/core';

// --- STATE ---
const canvasRef = ref<HTMLCanvasElement | null>(null);
const nodes = ref<GeoNode[]>([]);
const links = ref<GeoLink[]>([]);
const selectedNodeId = ref<string | null>(null);
const particles = ref<Particle[]>([]);

// --- COMPUTED ---
const activeNodeData = computed(() => 
  nodes.value.find(n => n.id === selectedNodeId.value)
);

// --- PHYSICS ENGINE (Optional if not using d3) ---
// Simple verlet or Euler integration for dust
const updatePhysics = () => {
   particles.value.forEach((p, i) => {
      // Logic from artifact...
   });
}

// --- RENDER LOOP ---
const render = () => {
  const ctx = canvasRef.value?.getContext('2d');
  if (!ctx) return;
  
  // 1. Clear & BG
  // 2. Draw Links
  // 3. Draw Nodes
  // 4. Draw Particles
  
  requestAnimationFrame(render);
}

onMounted(() => {
   initData();
   render();
});
</script>

<template>
  <div class="relative w-full h-screen bg-[#020408]">
    <!-- Canvas Layer -->
    <canvas ref="canvasRef" class="absolute inset-0 block" />

    <!-- UI Overlay Layer (HTML/Tailwind) -->
    <Transition name="fade">
      <div v-if="activeNodeData" 
           class="absolute glass-panel"
           :style="{ left: `${activeNodeData.x}px`, top: `${activeNodeData.y}px` }">
         <!-- Card Content -->
      </div>
    </Transition>
  </div>
</template>

<style scoped>
.glass-panel {
  @apply backdrop-blur-md bg-slate-900/80 border border-slate-700/50 rounded-xl shadow-2xl;
}
.fade-enter-active, .fade-leave-active { transition: opacity 0.3s ease; }
.fade-enter-from, .fade-leave-to { opacity: 0; }
</style>
```

## 5. API Data Structure (Mock)

При запросе `GET /graph/snapshot` сервер должен отдавать JSON, совместимый с d3-force:

```json
{
  "nodes": [
    { 
      "id": "node-1", 
      "type": "business", 
      "trustLimit": 5000, 
      "balance": -120,
      "coordinates": { "x": 0.5, "y": 0.5 } // Normalized 0-1 usually better
    }
  ],
  "links": [
    { "source": "node-1", "target": "node-2", "strength": 0.8 }
  ]
}
```


Нюанс по поводу force-graph
Библиотека force-graph — мощная, но она "рисует сама". Чтобы сохранить тот Ultra-вид (свечение, искры, специфические градиенты), который мы утвердили, вам придется использовать её API nodeCanvasObject и linkCanvasObject, чтобы перехватить рисование.

Моя рекомендация для вашего стека:
Используйте d3-force для физики (математики), но рисуйте на чистом Canvas (как в моем примере). Это даст вам 100% контроль над слоями (Bloom, Particles, UI lines) и лучшую производительность, чем попытка "натянуть" стандартный force-graph на этот дизайн.

Ниже я подготовил Техническое Задание (Spec Doc) для реализации этого визуала на Vue 3.

Как переносить?
Скопируйте код useEffect из моего финального React-артефакта. Это чистый JavaScript/Canvas код. Он ляжет внутрь функции render() во Vue без изменений.
Logic Separation: Во Vue удобно вынести логику физики (updateParticles, spawnExplosion) в отдельный файл useGeoPhysics.ts (composable), чтобы компонент занимался только отрисовкой и UI.
Tailwind: Классы для карточки UI (w-[240px], backdrop-blur-md, градиенты) скопируйте 1-в-1 в шаблон Vue.




import React, { useState, useEffect, useRef, useCallback } from 'react';
import { Users, Zap, Activity, ShieldCheck, ArrowRightLeft } from 'lucide-react';

// --- CONFIGURATION ---
const THEME = {
  bg: '#020408',
  nodeBusiness: '#10b981', // Emerald
  nodePerson: '#3b82f6',   // Blue
  gold: '#fbbf24',         // Amber (Clearing sparks)
  cyan: '#22d3ee',         // Active Link / Tx
  danger: '#ef4444',
  textSecondary: '#94a3b8'
};

const PHYSICS = {
  drag: 0.95, 
  dustLife: 0.015
};

// --- DATA GENERATION (Mesh Topology) ---
const generateData = () => {
  const nodes = [];
  const links = [];
  
  // 1. Создаем узлы (распределяем их органично)
  const count = 35;
  for (let i = 0; i < count; i++) {
    // Спиральное распределение для отсутствия наложений
    const angle = i * 0.5; // Golden angle approx
    const dist = 50 + (i * 7); // Равномерное удаление
    
    // Добавляем шум к координатам
    const x = 400 + Math.cos(angle) * dist + (Math.random() - 0.5) * 40;
    const y = 300 + Math.sin(angle) * dist * 0.8 + (Math.random() - 0.5) * 40;

    nodes.push({
      id: `n-${i}`,
      x: Math.max(50, Math.min(750, x)), // Clamp to screen
      y: Math.max(50, Math.min(550, y)),
      type: i % 4 === 0 ? 'business' : 'person', // 1 к 4 бизнесы
      baseSize: i % 4 === 0 ? 10 : 5,
      currentSize: i % 4 === 0 ? 10 : 5, // Для анимации увеличения
      name: i % 4 === 0 ? `BizNode ${i}` : `User ${i+100}`,
      trustLimit: Math.floor(Math.random() * 5000) + 1000,
      balance: Math.floor(Math.random() * 2000) - 1000, // Может быть отрицательным (долг)
      trustScore: 80 + Math.floor(Math.random() * 20)
    });
  }

  // 2. Создаем связи (Mesh)
  nodes.forEach((node, i) => {
    // Ищем ближайших соседей
    const neighbors = nodes
        .filter(n => n.id !== node.id)
        .map(n => ({ 
            id: n.id, 
            dist: Math.hypot(n.x - node.x, n.y - node.y) 
        }))
        .sort((a, b) => a.dist - b.dist)
        .slice(0, 3); // Соединяем с 3 ближайшими

    neighbors.forEach(neighbor => {
        // Избегаем дубликатов связей
        const linkExists = links.some(l => 
            (l.source === node.id && l.target === neighbor.id) ||
            (l.source === neighbor.id && l.target === node.id)
        );
        
        if (!linkExists) {
            links.push({
                source: node.id,
                target: neighbor.id,
                trust: Math.floor(Math.random() * 1000),
                active: false // Для подсветки
            });
        }
    });
  });

  return { nodes, links };
};

export default function GeoSimulatorMesh() {
  const canvasRef = useRef(null);
  const dataRef = useRef(generateData());
  const [selectedNode, setSelectedNode] = useState(dataRef.current.nodes[0]);
  const [isClearing, setIsClearing] = useState(false);
  const [uiTick, setUiTick] = useState(0); // Force UI re-render

  // VFX Refs
  const particlesRef = useRef([]); 
  const dustRef = useRef([]);      
  const timeRef = useRef(0);
  const reqRef = useRef();

  // --- LOGIC: EXPLOSIONS (Visual Feedback) ---
  const spawnExplosion = (x, y, colorHex, isBig = false) => {
    let c = colorHex.replace('#', '');
    const r = parseInt(c.substring(0,2), 16);
    const g = parseInt(c.substring(2,4), 16);
    const b = parseInt(c.substring(4,6), 16);
    const colorRGB = `${r}, ${g}, ${b}`;

    const count = isBig ? 30 : 15;
    for (let i = 0; i < count; i++) {
        const angle = Math.random() * Math.PI * 2;
        const speed = Math.random() * (isBig ? 3 : 1.5) + 0.5;
        dustRef.current.push({
            x, y,
            vx: Math.cos(angle) * speed,
            vy: Math.sin(angle) * speed,
            life: 1.0,
            decay: Math.random() * 0.02 + 0.01,
            size: Math.random() * 3 + 1,
            color: colorRGB
        });
    }
    // Shockwave ring
    dustRef.current.push({ x, y, type: 'shockwave', life: 1.0, size: 2, color: colorRGB });
  };

  // --- LOGIC: SINGLE TRANSACTION ---
  const triggerTransaction = useCallback(() => {
    const { nodes, links } = dataRef.current;
    
    // Ищем связи только выделенного узла (если есть), иначе случайную
    const activeId = selectedNode ? selectedNode.id : null;
    let relevantLinks = links;
    
    if (activeId) {
        relevantLinks = links.filter(l => l.source === activeId || l.target === activeId);
    }
    
    if (relevantLinks.length === 0) return;

    const link = relevantLinks[Math.floor(Math.random() * relevantLinks.length)];
    const src = nodes.find(n => n.id === link.source);
    const dst = nodes.find(n => n.id === link.target);
    
    if (src && dst) {
        // Определяем направление (от выделенного или к нему)
        const isOutgoing = src.id === activeId;
        const start = isOutgoing ? src : dst; // Если не выделен, то src
        const end = isOutgoing ? dst : src;

        particlesRef.current.push({
            x: start.x, y: start.y, tx: end.x, ty: end.y,
            color: THEME.cyan, type: 'simple', val: 100
        });
    }
  }, [selectedNode]);

  // --- LOGIC: CLEARING CYCLE (Netting) ---
  const triggerClearing = () => {
    if(isClearing) return;
    setIsClearing(true);
    
    let cycles = 0;
    const interval = setInterval(() => {
        const { nodes, links } = dataRef.current;
        // Эмуляция поиска циклов: запускаем частицы по треугольникам
        // Находим случайные треугольники в графе (простая эвристика)
        
        for(let k=0; k<2; k++) {
             const centerNode = nodes[Math.floor(Math.random() * nodes.length)];
             const nodeLinks = links.filter(l => l.source === centerNode.id || l.target === centerNode.id);
             
             if(nodeLinks.length >= 2) {
                 // Запускаем встречные потоки для "аннигиляции" долга
                 const l1 = nodeLinks[0];
                 const n1 = nodes.find(n => n.id === (l1.source === centerNode.id ? l1.target : l1.source));
                 
                 // Частица летит к центру
                 const midX = (centerNode.x + n1.x) / 2;
                 const midY = (centerNode.y + n1.y) / 2;
                 
                 particlesRef.current.push({ 
                    x: n1.x, y: n1.y, tx: midX, ty: midY, 
                    color: THEME.danger, type: 'clearing_part' 
                 });
                 particlesRef.current.push({ 
                    x: centerNode.x, y: centerNode.y, tx: midX, ty: midY, 
                    color: THEME.nodeBusiness, type: 'clearing_part' 
                 });
             }
        }

        cycles++;
        if (cycles > 10) {
            clearInterval(interval);
            setTimeout(() => {
                setIsClearing(false);
                // Обновляем баланс в UI (типа долги схлопнулись)
                if (selectedNode) {
                    selectedNode.balance = Math.floor(selectedNode.balance / 2); // Уменьшаем долг/кредит
                    setUiTick(p => p+1);
                }
            }, 1000);
        }
    }, 120);
  };

  // --- RENDER LOOP ---
  useEffect(() => {
    const canvas = canvasRef.current;
    const ctx = canvas.getContext('2d');
    ctx.imageSmoothingEnabled = true;
    ctx.imageSmoothingQuality = 'high';

    const render = () => {
      timeRef.current += 0.008;
      const t = timeRef.current;

      // 1. BG
      ctx.globalCompositeOperation = 'source-over';
      ctx.fillStyle = THEME.bg;
      ctx.fillRect(0, 0, 800, 600);

      // Bokeh spots
      const g1 = ctx.createRadialGradient(100, 500, 0, 100, 500, 400);
      g1.addColorStop(0, 'rgba(30, 64, 175, 0.12)'); g1.addColorStop(1, 'transparent');
      ctx.fillStyle = g1; ctx.fillRect(0,0,800,600);

      const g2 = ctx.createRadialGradient(700, 100, 0, 700, 100, 400);
      g2.addColorStop(0, 'rgba(6, 95, 70, 0.1)'); g2.addColorStop(1, 'transparent');
      ctx.fillStyle = g2; ctx.fillRect(0,0,800,600);

      // 2. NODES & LINKS CALCULATIONS
      // Анимация размера узлов
      dataRef.current.nodes.forEach(node => {
          const isSelected = selectedNode?.id === node.id;
          const targetSize = isSelected ? node.baseSize * 2 : node.baseSize; // Увеличение при клике
          // Lerp size
          node.currentSize += (targetSize - node.currentSize) * 0.1;
          
          // Floating
          node.renderX = node.x + Math.sin(t*1.5 + node.id.length) * 2;
          node.renderY = node.y + Math.cos(t*1.5 + node.id.length) * 2;
      });

      // 3. DRAW LINKS (Trust Lines)
      dataRef.current.links.forEach(link => {
        const s = dataRef.current.nodes.find(n => n.id === link.source);
        const d = dataRef.current.nodes.find(n => n.id === link.target);
        if (!s || !d) return;

        // Является ли связь частью активной сети выделенного узла?
        const isConnectedToSelected = selectedNode && (link.source === selectedNode.id || link.target === selectedNode.id);
        
        ctx.beginPath();
        ctx.moveTo(s.renderX, s.renderY);
        ctx.lineTo(d.renderX, d.renderY);

        if (isConnectedToSelected) {
             // ACTIVE TRUST LINE
             const grad = ctx.createLinearGradient(s.renderX, s.renderY, d.renderX, d.renderY);
             grad.addColorStop(0, '#3b82f6'); 
             grad.addColorStop(0.5, '#ffffff'); 
             grad.addColorStop(1, '#10b981');
             
             ctx.save();
             ctx.globalCompositeOperation = 'lighter';
             ctx.strokeStyle = grad;
             ctx.lineWidth = 1.5;
             ctx.shadowColor = '#22d3ee'; ctx.shadowBlur = 8;
             ctx.stroke();
             ctx.restore();
        } else {
             // PASSIVE MESH (Затухает, если есть выделение)
             const opacity = selectedNode ? 0.05 : 0.15;
             ctx.strokeStyle = `rgba(148, 163, 184, ${opacity})`;
             ctx.lineWidth = 0.5;
             ctx.stroke();
        }
      });

      // 4. DRAW NODES
      dataRef.current.nodes.forEach(node => {
        const isSelected = selectedNode?.id === node.id;
        const color = node.type === 'business' ? '#10b981' : '#3b82f6';
        
        // Bloom
        const bloomSize = node.currentSize * 4;
        const bloomAlpha = isSelected ? 0.4 : 0.2;
        const bloom = ctx.createRadialGradient(node.renderX, node.renderY, 0, node.renderX, node.renderY, bloomSize);
        bloom.addColorStop(0, node.type === 'business' ? `rgba(16, 185, 129, ${bloomAlpha})` : `rgba(59, 130, 246, ${bloomAlpha})`);
        bloom.addColorStop(1, 'transparent');
        ctx.fillStyle = bloom; ctx.fillRect(node.renderX - bloomSize, node.renderY - bloomSize, bloomSize*2, bloomSize*2);

        // Shape
        ctx.fillStyle = color;
        if (node.type === 'business') {
            const s = node.currentSize;
            ctx.fillRect(node.renderX - s/2, node.renderY - s/2, s, s);
            
            if (isSelected) {
                // Tech borders for selected business
                ctx.strokeStyle = 'rgba(16, 185, 129, 0.8)'; ctx.lineWidth = 1;
                ctx.strokeRect(node.renderX - s/2 - 4, node.renderY - s/2 - 4, s + 8, s + 8);
            }
        } else {
            ctx.beginPath(); ctx.arc(node.renderX, node.renderY, node.currentSize, 0, Math.PI * 2); ctx.fill();
            if (isSelected) {
                ctx.strokeStyle = 'rgba(59, 130, 246, 0.8)'; ctx.lineWidth = 1;
                ctx.beginPath(); ctx.arc(node.renderX, node.renderY, node.currentSize + 4, 0, Math.PI * 2); ctx.stroke();
            }
        }
      });

      // 5. PARTICLES
      ctx.globalCompositeOperation = 'lighter';
      for (let i = particlesRef.current.length - 1; i >= 0; i--) {
          const p = particlesRef.current[i];
          const dx = p.tx - p.x;
          const dy = p.ty - p.y;
          const dist = Math.sqrt(dx*dx + dy*dy);
          
          if (dist < 5) {
              particlesRef.current.splice(i, 1);
              spawnExplosion(p.tx, p.ty, p.type === 'clearing_part' ? THEME.gold : p.color, p.type === 'clearing_part');
              continue;
          }

          const speed = 5;
          p.x += (dx/dist) * speed;
          p.y += (dy/dist) * speed;

          // Comet
          ctx.shadowBlur = 15; ctx.shadowColor = p.color;
          ctx.fillStyle = '#ffffff'; 
          ctx.beginPath(); ctx.arc(p.x, p.y, 2.5, 0, Math.PI*2); ctx.fill();
          
          ctx.strokeStyle = p.color; ctx.lineWidth = 2;
          ctx.beginPath(); ctx.moveTo(p.x, p.y); ctx.lineTo(p.x - (dx/dist)*15, p.y - (dy/dist)*15); ctx.stroke();
          ctx.shadowBlur = 0;
      }

      // 6. DUST / EXPLOSIONS
      for (let i = dustRef.current.length - 1; i >= 0; i--) {
          const p = dustRef.current[i];
          if (p.type === 'shockwave') {
              p.life -= 0.04; p.size += 1.5;
              if (p.life <= 0) { dustRef.current.splice(i, 1); continue; }
              ctx.strokeStyle = `rgba(${p.color}, ${p.life})`; ctx.lineWidth = 1;
              ctx.beginPath(); ctx.arc(p.x, p.y, p.size, 0, Math.PI*2); ctx.stroke();
              continue;
          }
          p.x += p.vx; p.y += p.vy;
          p.vx *= PHYSICS.drag; p.vy *= PHYSICS.drag;
          p.life -= p.decay;
          if (p.life <= 0) { dustRef.current.splice(i, 1); continue; }
          
          const alpha = p.life;
          const size = p.size * alpha;
          const gradient = ctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, size * 2);
          gradient.addColorStop(0, `rgba(${p.color}, ${alpha})`);
          gradient.addColorStop(1, 'transparent');
          ctx.fillStyle = gradient; ctx.fillRect(p.x - size*2, p.y - size*2, size*4, size*4);
      }

      // 7. LINE TO CARD (Overlay)
      ctx.globalCompositeOperation = 'source-over';
      if (selectedNode) {
          const sn = selectedNode;
          const cardY = sn.renderY - 150;
          const grad = ctx.createLinearGradient(sn.renderX, sn.renderY - 20, sn.renderX, cardY + 80);
          grad.addColorStop(0, sn.type === 'business' ? THEME.nodeBusiness : THEME.nodePerson);
          grad.addColorStop(1, 'transparent');
          
          ctx.beginPath(); ctx.moveTo(sn.renderX, sn.renderY - (sn.currentSize+5));
          ctx.lineTo(sn.renderX, cardY + 80);
          ctx.strokeStyle = grad; ctx.lineWidth = 1.5; ctx.stroke();
      }

      reqRef.current = requestAnimationFrame(render);
    };
    render();
    return () => cancelAnimationFrame(reqRef.current);
  }, [selectedNode, isClearing]);

  // Click Handler
  const handleClick = (e) => {
    const rect = canvasRef.current.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const y = e.clientY - rect.top;
    
    // Find closest node
    const clicked = dataRef.current.nodes.find(n => 
        Math.hypot(n.renderX - x, n.renderY - y) < 30
    );
    if (clicked) setSelectedNode(clicked);
    else {
        // Если клик в пустоту - просто пускаем эффект, но не сбрасываем выделение (для удобства)
        triggerTransaction(); 
    }
  };

  const displayNode = selectedNode;

  return (
    <div className="relative w-full h-screen bg-[#020408] overflow-hidden flex justify-center items-center font-sans select-none text-white">
      
      <canvas 
        ref={canvasRef}
        width={800}
        height={600}
        onClick={handleClick}
        className="cursor-pointer"
      />

      {/* --- UI CARD (GEO Context) --- */}
      {displayNode && (
        <div 
            className="absolute z-20 pointer-events-none transition-all duration-200 ease-out"
            style={{ 
                left: displayNode.renderX - 120, 
                top: displayNode.renderY - 220 
            }}
        >
            <div className="w-[240px] rounded-lg p-0 relative overflow-hidden backdrop-blur-md shadow-2xl">
                {/* Backgrounds */}
                <div className="absolute inset-0 bg-[#0f172a] opacity-80 z-0"></div>
                <div className="absolute inset-0 border border-slate-700/50 rounded-lg z-10"></div>
                {/* Accent Lines */}
                <div className={`absolute top-0 left-0 right-0 h-[2px] z-20 ${displayNode.type === 'business' ? 'bg-emerald-500 shadow-[0_0_15px_#10b981]' : 'bg-blue-500 shadow-[0_0_15px_#3b82f6]'}`}></div>

                {/* Content */}
                <div className="relative z-20 p-4 space-y-3">
                    
                    {/* Header */}
                    <div className="flex justify-between items-start">
                        <div>
                            <div className="text-[10px] text-slate-400 uppercase tracking-widest font-bold mb-0.5">Node Identity</div>
                            <div className="text-white font-bold text-sm tracking-wide">{displayNode.name}</div>
                        </div>
                        {displayNode.type === 'business' 
                            ? <ShieldCheck size={18} className="text-emerald-400" /> 
                            : <Users size={18} className="text-blue-400" />
                        }
                    </div>

                    <div className="h-px bg-slate-700/50 w-full"></div>

                    {/* GEO Metrics */}
                    <div className="space-y-2">
                        {/* Trust Limit */}
                        <div className="flex justify-between items-center text-xs">
                            <span className="text-slate-400">Total Trust Limit</span>
                            <span className="text-slate-200 font-mono">{displayNode.trustLimit} GC</span>
                        </div>
                        
                        {/* Net Position (Balance) */}
                        <div className="flex justify-between items-center">
                            <span className="text-slate-400 text-xs">Net Position</span>
                            <span className={`font-mono font-bold text-lg ${displayNode.balance < 0 ? 'text-red-400' : 'text-emerald-400'}`}>
                                {displayNode.balance > 0 ? '+' : ''}{displayNode.balance}
                            </span>
                        </div>

                        {/* Trust Score Visual */}
                        <div className="mt-1">
                            <div className="flex justify-between text-[10px] text-slate-500 mb-1">
                                <span>Trust Score</span>
                                <span>{displayNode.trustScore}/100</span>
                            </div>
                            <div className="h-1 bg-slate-700 rounded-full overflow-hidden">
                                <div 
                                    className={`h-full ${displayNode.type === 'business' ? 'bg-emerald-500' : 'bg-blue-500'}`} 
                                    style={{ width: `${displayNode.trustScore}%` }}
                                ></div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
      )}

      {/* --- CONTROLS --- */}
      <div className="absolute bottom-8 z-30 flex gap-3">
         <button 
            onClick={triggerTransaction}
            className="flex items-center gap-2 px-5 py-2.5 bg-slate-800/80 border border-slate-600 rounded-lg hover:border-cyan-400 hover:text-cyan-400 transition-all text-xs font-bold uppercase tracking-wider backdrop-blur-sm"
        >
            <ArrowRightLeft size={14} /> Single Tx
         </button>

         <button 
            onClick={triggerClearing}
            disabled={isClearing}
            className={`
                flex items-center gap-2 px-6 py-2.5 rounded-lg border transition-all text-xs font-bold uppercase tracking-wider backdrop-blur-sm
                ${isClearing 
                    ? 'bg-amber-900/30 border-amber-500/50 text-amber-500' 
                    : 'bg-indigo-900/60 border-indigo-500/50 text-indigo-300 hover:bg-indigo-800/80 hover:text-white hover:border-indigo-400'}
            `}
        >
            <Activity size={14} className={isClearing ? 'animate-pulse' : ''} /> 
            {isClearing ? 'Calculating Cycles...' : 'Run Clearing Cycle'}
         </button>
      </div>

    </div>
  );
}

