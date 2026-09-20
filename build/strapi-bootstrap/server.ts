import type { Core } from '@strapi/strapi';

const config = ({ env }: Core.Config.Shared.ConfigParams): Core.Config.Server => ({
  host: env('HOST', '0.0.0.0'),
  port: env.int('PORT', 1337),
  app: {
    keys: env.array('APP_KEYS'),
  },
  // Native MCP server at /mcp (Strapi >= 5.47). Off unless the deployment opts in.
  mcp: {
    enabled: env.bool('MCP_ENABLED', false),
  },
});

export default config;
