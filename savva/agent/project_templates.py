"""Built-in project templates proposed by the agent, never written directly."""

from __future__ import annotations

import html


def build_savva_store_project(project_name: str, title: str) -> dict[str, str]:
    base = project_name.strip().strip("/")
    safe_title = html.escape(title.strip()[:120], quote=True)
    return {
        f"{base}/package.json": """{
  "name": "savva-computers",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc -b && vite build",
    "preview": "vite preview"
  },
  "dependencies": {
    "react": "^19.0.0",
    "react-dom": "^19.0.0"
  },
  "devDependencies": {
    "@types/react": "^19.0.0",
    "@types/react-dom": "^19.0.0",
    "@vitejs/plugin-react": "^5.0.0",
    "typescript": "^5.8.0",
    "vite": "^7.0.0"
  }
}
""",
        f"{base}/tsconfig.json": """{
  "files": [],
  "references": [
    { "path": "./tsconfig.app.json" },
    { "path": "./tsconfig.node.json" }
  ]
}
""",
        f"{base}/tsconfig.app.json": """{
  "compilerOptions": {
    "target": "ES2022",
    "useDefineForClassFields": true,
    "lib": ["ES2022", "DOM", "DOM.Iterable"],
    "allowJs": false,
    "skipLibCheck": true,
    "esModuleInterop": true,
    "allowSyntheticDefaultImports": true,
    "strict": true,
    "forceConsistentCasingInFileNames": true,
    "module": "ESNext",
    "moduleResolution": "Bundler",
    "resolveJsonModule": true,
    "isolatedModules": true,
    "noEmit": true,
    "jsx": "react-jsx"
  },
  "include": ["src"]
}
""",
        f"{base}/tsconfig.node.json": """{
  "compilerOptions": {
    "composite": true,
    "skipLibCheck": true,
    "module": "ESNext",
    "moduleResolution": "Bundler",
    "allowImportingTsExtensions": true
  },
  "include": ["vite.config.ts"]
}
""",
        f"{base}/vite.config.ts": """import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
});
""",
        f"{base}/index.html": f"""<!doctype html>
<html lang="ru">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <meta
      name="description"
      content="{safe_title} — компьютеры SAVVA GAME, HOME и AI."
    />
    <title>{safe_title}</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
""",
        f"{base}/src/vite-env.d.ts": '/// <reference types="vite/client" />\n',
        f"{base}/src/main.tsx": """import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./styles.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
""",
        f"{base}/src/data/computers.ts": """export type ComputerSeries = {
  id: string;
  name: string;
  audience: string;
  description: string;
  accent: string;
};

export const computers: ComputerSeries[] = [
  {
    id: "game",
    name: "SAVVA GAME",
    audience: "Для игр и динамичных задач",
    description:
      "Игровая линейка SAVVA. Точная конфигурация и стоимость подтверждаются менеджером после согласования задач.",
    accent: "violet",
  },
  {
    id: "home",
    name: "SAVVA HOME",
    audience: "Для работы, учёбы и повседневных задач",
    description:
      "Сбалансированная домашняя система. Состав компонентов подбирается под задачи без неподтверждённых обещаний.",
    accent: "green",
  },
  {
    id: "ai",
    name: "SAVVA AI",
    audience: "Для локальных AI-экспериментов",
    description:
      "Платформа для обсуждения AI-нагрузок. Требования к GPU, памяти и охлаждению фиксируются после технического согласования.",
    accent: "blue",
  },
];
""",
        f"{base}/src/components/SiteHeader.tsx": """export function SiteHeader() {
  return (
    <header className="site-header">
      <a className="brand" href="#top" aria-label="SAVVA Computers">
        <span className="brand-mark">S</span>
        <span>
          <strong>SAVVA</strong>
          <small>COMPUTERS</small>
        </span>
      </a>
      <nav aria-label="Основная навигация">
        <a href="#catalog">Каталог</a>
        <a href="#approach">Подход</a>
        <a href="#contact">Связаться</a>
      </nav>
    </header>
  );
}
""",
        f"{base}/src/components/Hero.tsx": """export function Hero() {
  return (
    <section className="hero" id="top">
      <div className="hero-copy">
        <p className="eyebrow">СИСТЕМЫ, СОБРАННЫЕ ВОКРУГ ЗАДАЧИ</p>
        <h1>Компьютер SAVVA начинается с точных требований.</h1>
        <p className="hero-text">
          GAME, HOME и AI — три направления для разных сценариев.
          Конфигурация и цена обсуждаются отдельно, без вымышленных
          характеристик на сайте.
        </p>
        <div className="hero-actions">
          <a className="button primary" href="#catalog">Смотреть серии</a>
          <a className="button secondary" href="#contact">Обсудить задачу</a>
        </div>
      </div>
      <div className="hero-visual" aria-hidden="true">
        <div className="tower">
          <span>SAVVA</span>
          <i />
          <i />
          <i />
        </div>
        <div className="signal-line one" />
        <div className="signal-line two" />
      </div>
    </section>
  );
}
""",
        f"{base}/src/components/ComputerCard.tsx": """import type { ComputerSeries } from "../data/computers";

type Props = {
  computer: ComputerSeries;
};

export function ComputerCard({ computer }: Props) {
  return (
    <article className={`computer-card ${computer.accent}`}>
      <div className="card-number">0{computer.id === "game" ? 1 : computer.id === "home" ? 2 : 3}</div>
      <p className="card-audience">{computer.audience}</p>
      <h3>{computer.name}</h3>
      <p>{computer.description}</p>
      <div className="card-footer">
        <span>Цена уточняется</span>
        <a href="#contact" aria-label={`Обсудить ${computer.name}`}>→</a>
      </div>
    </article>
  );
}
""",
        f"{base}/src/components/CatalogSection.tsx": """import { computers } from "../data/computers";
import { ComputerCard } from "./ComputerCard";

export function CatalogSection() {
  return (
    <section className="catalog" id="catalog">
      <div className="section-heading">
        <p className="eyebrow">КАТАЛОГ</p>
        <h2>Три направления SAVVA</h2>
        <p>
          Выберите сценарий. Точные характеристики и стоимость появятся
          только после подтверждения комплектации.
        </p>
      </div>
      <div className="card-grid">
        {computers.map((computer) => (
          <ComputerCard computer={computer} key={computer.id} />
        ))}
      </div>
    </section>
  );
}
""",
        f"{base}/src/components/ApproachSection.tsx": """const steps = [
  {
    number: "01",
    title: "Задача",
    text: "Фиксируем программы, игры и рабочие сценарии.",
  },
  {
    number: "02",
    title: "Конфигурация",
    text: "Подбираем компоненты и проверяем совместимость.",
  },
  {
    number: "03",
    title: "Подтверждение",
    text: "Согласуем точный состав, цену и сроки до заказа.",
  },
];

export function ApproachSection() {
  return (
    <section className="approach" id="approach">
      <div className="section-heading compact">
        <p className="eyebrow">ПОДХОД</p>
        <h2>Сначала требования, затем цена</h2>
      </div>
      <div className="step-grid">
        {steps.map((step) => (
          <article key={step.number}>
            <span>{step.number}</span>
            <h3>{step.title}</h3>
            <p>{step.text}</p>
          </article>
        ))}
      </div>
    </section>
  );
}
""",
        f"{base}/src/components/ContactSection.tsx": """export function ContactSection() {
  return (
    <section className="contact" id="contact">
      <div>
        <p className="eyebrow">СЛЕДУЮЩИЙ ШАГ</p>
        <h2>Опишите задачу для будущего компьютера.</h2>
      </div>
      <a className="button primary" href="mailto:hello@savva.example">
        Обсудить конфигурацию
      </a>
    </section>
  );
}
""",
        f"{base}/src/App.tsx": """import { ApproachSection } from "./components/ApproachSection";
import { CatalogSection } from "./components/CatalogSection";
import { ContactSection } from "./components/ContactSection";
import { Hero } from "./components/Hero";
import { SiteHeader } from "./components/SiteHeader";

export default function App() {
  return (
    <>
      <div className="page-glow" aria-hidden="true" />
      <SiteHeader />
      <main>
        <Hero />
        <CatalogSection />
        <ApproachSection />
        <ContactSection />
      </main>
      <footer>
        <span>SAVVA Computers</span>
        <span>Характеристики и цены требуют подтверждения.</span>
      </footer>
    </>
  );
}
""",
        f"{base}/src/styles.css": """:root {
  color: #e8f5ef;
  background: #07100e;
  font-family: "Segoe UI Variable", "Segoe UI", sans-serif;
  font-synthesis: none;
  --accent: #72edb7;
  --line: rgba(183, 230, 209, 0.16);
  --muted: #94aaa1;
}

* { box-sizing: border-box; }
html { scroll-behavior: smooth; }
body { margin: 0; min-width: 320px; min-height: 100vh; }
body {
  background:
    radial-gradient(circle at 12% 0%, rgba(64, 188, 139, 0.2), transparent 32rem),
    linear-gradient(160deg, #07100e, #0a1512 55%, #060b0a);
}
a { color: inherit; text-decoration: none; }
button, a { -webkit-tap-highlight-color: transparent; }
.page-glow {
  position: fixed;
  inset: 0;
  pointer-events: none;
  background: linear-gradient(120deg, transparent 30%, rgba(255,255,255,.025), transparent 70%);
}
.site-header {
  width: min(1180px, calc(100% - 40px));
  margin: 0 auto;
  min-height: 82px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-bottom: 1px solid var(--line);
}
.brand { display: flex; align-items: center; gap: 11px; letter-spacing: .12em; }
.brand-mark {
  width: 38px; height: 38px; display: grid; place-items: center;
  border-radius: 11px; color: #04110c; background: var(--accent);
  font-weight: 900;
}
.brand small { display: block; color: var(--muted); font-size: 9px; }
nav { display: flex; gap: 25px; color: var(--muted); font-size: 14px; }
nav a:hover { color: var(--accent); }
main { width: min(1180px, calc(100% - 40px)); margin: 0 auto; }
.hero {
  min-height: 680px;
  display: grid;
  grid-template-columns: 1.15fr .85fr;
  align-items: center;
  gap: 60px;
}
.eyebrow { color: var(--accent); font-size: 11px; font-weight: 800; letter-spacing: .16em; }
h1, h2, h3, p { margin-top: 0; }
h1 { max-width: 760px; font-size: clamp(48px, 7vw, 92px); line-height: .94; letter-spacing: -.055em; }
.hero-text { max-width: 590px; color: var(--muted); font-size: 17px; line-height: 1.75; }
.hero-actions { display: flex; gap: 12px; margin-top: 30px; flex-wrap: wrap; }
.button { padding: 13px 20px; border: 1px solid var(--line); border-radius: 12px; }
.button.primary { color: #04110c; background: var(--accent); border-color: var(--accent); font-weight: 800; }
.button.secondary { color: var(--muted); }
.hero-visual { min-height: 420px; position: relative; display: grid; place-items: center; }
.tower {
  width: 190px; height: 330px; display: flex; flex-direction: column;
  align-items: center; gap: 24px; padding: 30px;
  border: 1px solid rgba(114,237,183,.45); border-radius: 28px;
  background: linear-gradient(145deg, rgba(114,237,183,.12), rgba(0,0,0,.3));
  box-shadow: 0 40px 120px rgba(38, 174, 119, .18);
  transform: rotate(5deg);
}
.tower span { letter-spacing: .18em; font-weight: 900; }
.tower i { width: 100%; height: 1px; background: linear-gradient(90deg, transparent, var(--accent), transparent); }
.signal-line { position: absolute; width: 220px; height: 1px; background: var(--line); }
.one { transform: rotate(35deg); }
.two { transform: rotate(-45deg); }
.catalog, .approach { padding: 100px 0; border-top: 1px solid var(--line); }
.section-heading { display: grid; grid-template-columns: 220px 1fr; gap: 12px 30px; margin-bottom: 40px; }
.section-heading h2 { font-size: clamp(34px, 5vw, 60px); line-height: 1; letter-spacing: -.04em; }
.section-heading > p:last-child { grid-column: 2; max-width: 620px; color: var(--muted); line-height: 1.7; }
.section-heading.compact > p:last-child { display: none; }
.card-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; }
.computer-card {
  min-height: 370px; padding: 26px; display: flex; flex-direction: column;
  border: 1px solid var(--line); border-radius: 22px;
  background: rgba(255,255,255,.018);
  transition: transform .2s ease, border-color .2s ease;
}
.computer-card:hover { transform: translateY(-5px); border-color: rgba(114,237,183,.5); }
.computer-card.violet { background: linear-gradient(155deg, rgba(130,100,255,.14), transparent 55%), rgba(255,255,255,.018); }
.computer-card.green { background: linear-gradient(155deg, rgba(79,211,145,.14), transparent 55%), rgba(255,255,255,.018); }
.computer-card.blue { background: linear-gradient(155deg, rgba(80,160,255,.14), transparent 55%), rgba(255,255,255,.018); }
.card-number { margin-left: auto; color: var(--muted); font: 12px Consolas, monospace; }
.card-audience { margin-top: 45px; color: var(--accent); font-size: 12px; }
.computer-card h3 { font-size: 30px; margin-bottom: 14px; }
.computer-card p { color: var(--muted); line-height: 1.65; }
.card-footer { margin-top: auto; padding-top: 20px; border-top: 1px solid var(--line); display: flex; justify-content: space-between; }
.card-footer a { color: var(--accent); font-size: 22px; }
.step-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; }
.step-grid article { padding: 28px; border-top: 1px solid var(--line); }
.step-grid span { color: var(--accent); font: 12px Consolas, monospace; }
.step-grid h3 { margin-top: 48px; font-size: 24px; }
.step-grid p { color: var(--muted); line-height: 1.7; }
.contact {
  margin: 30px 0 90px; padding: clamp(30px, 6vw, 70px);
  display: flex; align-items: center; justify-content: space-between; gap: 30px;
  border: 1px solid rgba(114,237,183,.35); border-radius: 26px;
  background: linear-gradient(130deg, rgba(114,237,183,.12), transparent 65%);
}
.contact h2 { max-width: 760px; margin-bottom: 0; font-size: clamp(32px, 5vw, 58px); line-height: 1; letter-spacing: -.04em; }
footer {
  width: min(1180px, calc(100% - 40px)); margin: 0 auto; padding: 24px 0 38px;
  display: flex; justify-content: space-between; gap: 20px;
  border-top: 1px solid var(--line); color: var(--muted); font-size: 12px;
}
@media (max-width: 900px) {
  .hero { grid-template-columns: 1fr; padding: 70px 0; }
  .hero-visual { min-height: 300px; }
  .card-grid, .step-grid { grid-template-columns: 1fr; }
  .section-heading { grid-template-columns: 1fr; }
  .section-heading > p:last-child { grid-column: auto; }
  .contact { align-items: flex-start; flex-direction: column; }
}
@media (max-width: 620px) {
  nav { display: none; }
  .site-header, main, footer { width: min(100% - 24px, 1180px); }
  h1 { font-size: clamp(44px, 15vw, 68px); }
  .hero { min-height: auto; }
  .computer-card { min-height: 330px; }
  footer { flex-direction: column; }
}
""",
        f"{base}/README.md": f"""# {safe_title}

React + TypeScript + Vite storefront proposal for SAVVA GAME, HOME, and AI.

This project was created from the SAVVA controlled template. Prices and exact
technical specifications are intentionally marked as requiring confirmation;
they are not presented as verified commercial data.

No dependencies have been installed and no build or development server has
been started.

## Structure

- `src/components` — page sections and product cards
- `src/data/computers.ts` — GAME, HOME, and AI catalog data
- `src/styles.css` — responsive interface

## Next approval-gated action

After reviewing the project, explicitly approve dependency installation and
local execution in a future execution-enabled WIP.
""",
        f"{base}/.gitignore": """node_modules/
dist/
.vite/
*.local
.env
.env.*
""",
    }
