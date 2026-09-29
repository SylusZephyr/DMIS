// Typed access to the platform API, derived from the generated OpenAPI types (lib/api-schema.d.ts,
// `npm run gen:api`). What is precise today:
//   * route templates: a typo in a path or a missing path parameter is a compile error;
//   * request bodies: every Pydantic request model (LaunchRequest, ScopeIn, ProjectIn, ...) is checked.
// What is not: most GET handlers return plain dicts without `response_model`, so their response types are
// `unknown` and the hand-written types in lib/api.ts stay the source of truth for responses. Path to full
// typing: add Pydantic response models (or `response_model=`) to the FastAPI routes, regenerate, then
// replace the hand-written types in lib/api.ts with `Schemas["..."]` aliases route by route.
import type { components, paths } from "./api-schema";
import { post } from "./api";

export type Schemas = components["schemas"];
type Prefixed = keyof paths & `/api/v2${string}`;
/** A route under /api/v2, as a template (e.g. "/markets/{market}/scope"). */
export type ApiRoute = Prefixed extends `/api/v2${infer R}` ? R : never;
type Item<R extends ApiRoute> = paths[`/api/v2${R}`];
type PostOp<R extends ApiRoute> = NonNullable<Item<R>["post"]>;
export type PostRoute = { [R in ApiRoute]: [NonNullable<Item<R>["post"]>] extends [never] ? never : R }[ApiRoute];

/** Path parameters of a route template: "/a/{x}/b/{y}" -> { x: string | number; y: string | number }. */
export type RouteParams<R extends string> = R extends `${string}{${infer P}}${infer Rest}` ? { [K in P]: string | number } & RouteParams<Rest> : unknown;
type HasParams<R extends string> = R extends `${string}{${string}}${string}` ? true : false;

type BodyOf<Op> = Op extends { requestBody: { content: infer C } } | { requestBody?: { content: infer C } }
  ? C extends { "application/json": infer B } ? B : C extends { "multipart/form-data": unknown } ? FormData : never
  : Record<string, never>;
export type PostBody<R extends PostRoute> = BodyOf<PostOp<R>>;
export type PostResult<R extends PostRoute> = PostOp<R> extends { responses: { 200: { content: { "application/json": infer T } } } } ? T : unknown;

/** Fill a route template: apiPath("/markets/{market}/scope", { market: "denture base" }) -> "/markets/denture%20base/scope". */
export function apiPath<R extends ApiRoute>(route: R, ...params: HasParams<R> extends true ? [RouteParams<R>] : []): string {
  const values = (params[0] ?? {}) as Record<string, string | number>;
  return route.replace(/\{([^}]+)\}/g, (_, k: string) => {
    if (!(k in values)) throw new Error(`apiPath: missing parameter "${k}" for ${route}`);
    return encodeURIComponent(String(values[k]));
  });
}

/** POST with a body checked against the backend's request model. Mutation semantics are post()'s (cache invalidation). */
export function apiPost<R extends PostRoute>(route: R, body: PostBody<R>, ...params: HasParams<R> extends true ? [RouteParams<R>] : []): Promise<PostResult<R>> {
  const path = (apiPath as (r: string, p?: unknown) => string)(route, params[0]);
  return post<PostResult<R>>(path, body);
}
