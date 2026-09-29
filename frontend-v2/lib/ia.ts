// Information architecture of v2: task-oriented workspaces. One definition drives the workspace rail, the
// section panel, breadcrumbs, the command palette and the workspace hub pages, so a page added here appears
// everywhere at once. Labels and descriptions are i18n keys (messages/*.ts: nav.* and ia.*).
import type { LucideIcon } from "lucide-react";
import {
  Activity, BookOpen, Building2, ClipboardCheck, ClipboardList, Columns2, Compass, Database, Factory, FolderKanban, Gauge,
  Globe2, Home, Inbox, LayoutGrid, Lightbulb, ListChecks, Map as MapIcon, MessageSquareText, Network, Orbit, Rocket, Search,
  Settings2, ShieldCheck, Calculator, PackageSearch, PlugZap, ShoppingBag, Sparkles, Swords, Target, Trophy, Upload, UserRound, Bell, Hammer, Eye, Tags, Receipt,
} from "lucide-react";

export type PageDef = {
  href: string;
  label: string;          // i18n key
  desc: string;           // i18n key
  icon: LucideIcon;
  keywords?: string;      // extra words the command palette matches (both languages)
  marketScoped?: boolean; // the page follows the global market context
  hidden?: boolean;       // reachable (palette, links) but not listed in the section panel
};
export type Workspace = { id: string; href: string; label: string; desc: string; icon: LucideIcon; pages: PageDef[] };

export const WORKSPACES: Workspace[] = [
  { id: "home", href: "/", label: "ia.home", desc: "ia.homeDesc", icon: Home, pages: [
    { href: "/", label: "ia.missionControl", desc: "ia.missionControlDesc", icon: Gauge, keywords: "home dashboard overview 首页 总览" },
    { href: "/command", label: "nav.command", desc: "ia.commandDesc", icon: Globe2, keywords: "globe world map geo 地球 全球" },
  ]},
  { id: "discover", href: "/discover", label: "ia.discover", desc: "ia.discoverDesc", icon: Compass, pages: [
    { href: "/search", label: "nav.search", desc: "ia.searchDesc", icon: Search, keywords: "find lookup 搜索" },
    { href: "/markets", label: "nav.markets", desc: "ia.marketsDesc", icon: LayoutGrid, marketScoped: true, keywords: "market size segments analytics 市场 细分" },
    { href: "/intelligence", label: "nav.intelMap", desc: "ia.intelDesc", icon: MapIcon, marketScoped: true, keywords: "taxonomy capacity heatmap leaf 分类 容量 热力图" },
    { href: "/keywords", label: "nav.keywords", desc: "ia.keywordsDesc", icon: Tags, marketScoped: true, keywords: "keyword search volume demand ppc bid seo 关键词 搜索量 流量" },
    { href: "/graph", label: "nav.graph", desc: "ia.graphDesc", icon: Network, keywords: "knowledge graph relations 知识图谱" },
    { href: "/universe", label: "nav.universe", desc: "ia.universeDesc", icon: Orbit, keywords: "3d universe 宇宙" },
    { href: "/galaxy", label: "nav.galaxy", desc: "ia.galaxyDesc", icon: Sparkles, marketScoped: true, keywords: "3d products galaxy 星系" },
  ]},
  { id: "decide", href: "/decide", label: "ia.decide", desc: "ia.decideDesc", icon: Lightbulb, pages: [
    { href: "/opportunities", label: "nav.opportunities", desc: "ia.boardDesc", icon: Trophy, marketScoped: true, keywords: "opportunity ranking concepts score 机会 看板" },
    { href: "/compare", label: "nav.compare", desc: "ia.compareDesc", icon: Columns2, keywords: "side by side versus 对比" },
    { href: "/launch", label: "nav.launch", desc: "ia.launchDesc", icon: Rocket, marketScoped: true, keywords: "simulate forecast launch 模拟 上新" },
    { href: "/competitors", label: "nav.competitors", desc: "ia.competitorsDesc", icon: Swords, marketScoped: true, keywords: "brands share trends 竞品 品牌" },
    { href: "/watchlist", label: "nav.watchlist", desc: "ia.watchlistDesc", icon: Eye, keywords: "watch track competitor asin price alert 监控 竞品 跟踪" },
    { href: "/analyst", label: "nav.analyst", desc: "ia.analystDesc", icon: MessageSquareText, keywords: "ask question ai chat 分析师 问答" },
    { href: "/shop", label: "nav.shop", desc: "ia.shopDesc", icon: ShoppingBag, keywords: "buy recommend need 采购 推荐" },
    { href: "/economics", label: "nav.economics", desc: "ia.economicsDesc", icon: Calculator, keywords: "profit margin landed cost fba acos 利润 成本 计算" },
  ]},
  { id: "execute", href: "/execute", label: "ia.execute", desc: "ia.executeDesc", icon: Hammer, pages: [
    { href: "/sourcing", label: "nav.sourcing", desc: "ia.sourcingDesc", icon: PackageSearch, keywords: "supplier 1688 alibaba factory rfq quote 供应商 工厂 采购 寻源" },
    { href: "/projects", label: "nav.projects", desc: "ia.projectsDesc", icon: FolderKanban, keywords: "pipeline kanban project approve 项目 流程" },
    { href: "/suppliers", label: "nav.suppliers", desc: "ia.suppliersDesc", icon: Factory, keywords: "factory oem odm sourcing 供应商 工厂" },
    { href: "/own-sales", label: "nav.ownSales", desc: "ia.ownSalesDesc", icon: Receipt, keywords: "seller central business report actual sales accuracy 自有销量 业务报告 实际销量" },
    { href: "/alerts", label: "nav.alerts", desc: "ia.alertsDesc", icon: Bell, keywords: "events notifications 警报 事件" },
    { href: "/inbox", label: "nav.inbox", desc: "ia.inboxDesc", icon: Inbox, keywords: "messages 收件箱 消息" },
    { href: "/portfolio", label: "nav.portfolio", desc: "ia.portfolioDesc", icon: UserRound, keywords: "my markets focus 我的" },
  ]},
  { id: "quality", href: "/quality", label: "ia.quality", desc: "ia.qualityDesc", icon: ShieldCheck, pages: [
    { href: "/import", label: "nav.importData", desc: "ia.importDesc", icon: Upload, keywords: "upload preview columns 导入 上传" },
    { href: "/data", label: "nav.data", desc: "ia.dataDesc", icon: Database, keywords: "jobs datasets freshness snapshots 数据 任务" },
    { href: "/connectors", label: "nav.connectors", desc: "ia.connectorsDesc", icon: PlugZap, keywords: "live amazon keepa api scraping reviews connectors 实时 数据源 接口" },
    { href: "/review", label: "nav.review", desc: "ia.reviewDesc", icon: ListChecks, marketScoped: true, keywords: "duplicates taxonomy anomalies relevance 审核 重复" },
    { href: "/accuracy", label: "nav.accuracy", desc: "ia.accuracyDesc", icon: Target, keywords: "backtest calibration benchmark 准确 校准" },
    { href: "/labelling", label: "nav.labelling", desc: "ia.labellingDesc", icon: ClipboardCheck, keywords: "labels sample ground truth 标注" },
    { href: "/pilot", label: "nav.pilotPage", desc: "ia.pilotDesc", icon: Activity, keywords: "feedback usage telemetry 反馈 使用" },
  ]},
  { id: "admin", href: "/admin", label: "ia.admin", desc: "ia.adminDesc", icon: Settings2, pages: [
    { href: "/org", label: "nav.org", desc: "ia.orgDesc", icon: Building2, keywords: "users roles tokens plan 组织 用户" },
    { href: "/audit", label: "nav.audit", desc: "ia.auditDesc", icon: ClipboardList, keywords: "log history 审计 日志" },
    { href: "/methodology", label: "nav.methodology", desc: "ia.methodologyDesc", icon: BookOpen, keywords: "formula how method 方法论 公式" },
  ]},
];

export const ALL_PAGES: (PageDef & { workspace: Workspace })[] = WORKSPACES.flatMap((w) => w.pages.map((p) => ({ ...p, workspace: w })));

function matches(path: string, href: string) {
  return href === "/" ? path === "/" : path === href || path.startsWith(`${href}/`);
}

/** The workspace and page a path belongs to (longest matching page href wins). Hubs map to their workspace. */
export function locate(path: string): { workspace: Workspace; page: PageDef | null } {
  const hub = WORKSPACES.find((w) => w.href !== "/" && matches(path, w.href));
  let best: (PageDef & { workspace: Workspace }) | null = null;
  for (const p of ALL_PAGES) if (matches(path, p.href) && (!best || p.href.length > best.href.length)) best = p;
  if (best && !(hub && best.href === "/")) return { workspace: best.workspace, page: best };
  return { workspace: hub ?? WORKSPACES[0], page: null };
}

/** Link targets that carry the selected market (the same rule as v1's switcher). */
export function withMarket(href: string, market: string): string {
  if (!market) return href;
  const enc = encodeURIComponent(market);
  switch (href) {
    case "/markets": return `/markets/${enc}`;
    case "/galaxy": return `/galaxy/${enc}`;
    case "/competitors": case "/launch": case "/opportunities": case "/intelligence": case "/review": case "/sourcing": case "/keywords": return `${href}?market=${enc}`;
    default: return href;
  }
}

