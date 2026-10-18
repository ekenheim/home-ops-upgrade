// Runs as Vane's init container, before the server starts.
//
// Vane (v1.12) keeps every setting in data/config.json and normally fills it
// from a first-run setup screen. For an OpenAI-compatible base URL that is not
// api.openai.com it lists no models at all until someone adds them by hand in
// that screen. This script makes the part we care about declarative instead:
// on every start it upserts one provider ("LiteLLM") with the key, the base
// URL and the model list below, sets the SearXNG URL, and marks setup done.
// Everything else in the file (theme, personalization, any provider added in
// the UI) is left alone, but edits to the LiteLLM provider itself are reset
// here on the next restart: change the lists below instead.
//
// No template literals here: Flux's postBuild substitution would read them
// as variables (the ConfigMap is built by this Kustomization).
//
// Field layout follows src/lib/config/{index,types}.ts at v1.12.2; recheck it
// when bumping the image.
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';

const dataDir = path.join(process.env.DATA_DIR || '/home/vane', 'data');
const configPath = path.join(dataDir, 'config.json');

const required = (name) => {
  const value = process.env[name];
  if (!value) {
    console.error('seed: ' + name + ' is not set');
    process.exit(1);
  }
  return value;
};

const providerConfig = {
  apiKey: required('LITELLM_API_KEY'),
  baseURL: required('LITELLM_BASE_URL'),
};

// Vane's own hashObj (src/lib/utils/hash.ts). It dedupes env-defined
// providers by this hash; matching it keeps the record looking native.
const hashObj = (obj) =>
  crypto
    .createHash('sha256')
    .update(JSON.stringify(obj, Object.keys(obj).sort()))
    .digest('hex');

// Aliases the `vane` LiteLLM key may call (litellm/keys/vane.yaml). Local
// only: questions typed here can be personal.
const provider = {
  id: 'litellm',
  name: 'LiteLLM',
  type: 'openai',
  chatModels: [{ name: 'fast (local, worker4)', key: 'fast' }],
  embeddingModels: [{ name: 'Qwen3 Embedding 0.6B', key: 'qwen3-embedding-0.6b' }],
  config: providerConfig,
  hash: hashObj(providerConfig),
};

let config = {
  version: 1,
  setupComplete: false,
  preferences: {},
  personalization: {},
  modelProviders: [],
  search: { searxngURL: '' },
};

fs.mkdirSync(dataDir, { recursive: true });
if (fs.existsSync(configPath)) {
  try {
    config = { ...config, ...JSON.parse(fs.readFileSync(configPath, 'utf-8')) };
  } catch (err) {
    console.error('seed: ' + configPath + ' is not valid JSON, starting over:', err.message);
  }
}

config.modelProviders = [
  provider,
  ...(config.modelProviders || []).filter((p) => p.id !== provider.id),
];
config.search = { ...(config.search || {}), searxngURL: required('SEARXNG_API_URL') };
config.setupComplete = true;

// The file holds the LiteLLM key.
fs.writeFileSync(configPath, JSON.stringify(config, null, 2), { mode: 0o600 });
fs.chmodSync(configPath, 0o600);
console.log('seed: wrote ' + configPath + ' (' + config.modelProviders.length + ' provider(s))');
