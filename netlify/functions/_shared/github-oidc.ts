import { createPublicKey, createVerify, type JsonWebKey } from "node:crypto";

interface JwtHeader {
  alg?: string;
  kid?: string;
}

interface JwtClaims {
  iss?: string;
  aud?: string | string[];
  exp?: number;
  nbf?: number;
  repository?: string;
  repository_id?: string;
  ref?: string;
  workflow_ref?: string;
  event_name?: string;
}

interface Jwk {
  kid?: string;
  kty?: string;
  alg?: string;
  use?: string;
  n?: string;
  e?: string;
}

const ISSUER = "https://token.actions.githubusercontent.com";
const AUDIENCE = "stock-watch-telegram-bot-migration";
const REPOSITORY = "noamfurer/stock-watch-telegram-bot";
const REPOSITORY_ID = "1347689643";
const WORKFLOW_REF = `${REPOSITORY}/.github/workflows/migrate-to-netlify.yml@refs/heads/main`;

function decodeJson<T>(segment: string): T {
  return JSON.parse(Buffer.from(segment, "base64url").toString("utf8")) as T;
}

async function signingKey(kid: string) {
  const response = await fetch(`${ISSUER}/.well-known/jwks`, { signal: AbortSignal.timeout(8_000) });
  if (!response.ok) throw new Error("Could not load GitHub OIDC keys");
  const payload = await response.json() as { keys?: Jwk[] };
  const jwk = payload.keys?.find((candidate) => candidate.kid === kid && candidate.kty === "RSA");
  if (!jwk) throw new Error("GitHub OIDC signing key was not found");
  return createPublicKey({ key: jwk as JsonWebKey, format: "jwk" });
}

export async function verifyGitHubMigrationRequest(request: Request): Promise<boolean> {
  try {
    const authorization = request.headers.get("authorization") ?? "";
    if (!authorization.startsWith("Bearer ")) return false;
    const token = authorization.slice(7).trim();
    const parts = token.split(".");
    if (parts.length !== 3) return false;
    const [encodedHeader, encodedClaims, encodedSignature] = parts as [string, string, string];
    const header = decodeJson<JwtHeader>(encodedHeader);
    const claims = decodeJson<JwtClaims>(encodedClaims);
    if (header.alg !== "RS256" || !header.kid) return false;

    const verifier = createVerify("RSA-SHA256");
    verifier.update(`${encodedHeader}.${encodedClaims}`);
    verifier.end();
    if (!verifier.verify(await signingKey(header.kid), Buffer.from(encodedSignature, "base64url"))) return false;

    const now = Math.floor(Date.now() / 1000);
    const audiences = Array.isArray(claims.aud) ? claims.aud : [claims.aud];
    return claims.iss === ISSUER &&
      audiences.includes(AUDIENCE) &&
      typeof claims.exp === "number" && claims.exp >= now && claims.exp <= now + 600 &&
      (claims.nbf === undefined || claims.nbf <= now + 30) &&
      claims.repository === REPOSITORY &&
      claims.repository_id === REPOSITORY_ID &&
      claims.ref === "refs/heads/main" &&
      claims.workflow_ref === WORKFLOW_REF &&
      ["push", "workflow_dispatch"].includes(claims.event_name ?? "");
  } catch {
    return false;
  }
}
