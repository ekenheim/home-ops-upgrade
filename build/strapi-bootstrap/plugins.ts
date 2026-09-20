import type { Core } from '@strapi/strapi';

// Translate plugin configuration — uses DeepL provider with API key from env var.
const config = ({ env }: Core.Config.Shared.ConfigParams): Core.Config.Plugin => ({
  translate: {
    enabled: true,
    config: {
      provider: 'deepl',
      providerOptions: {
        apiKey: env('DEEPL_API_KEY'),
        freeApi: true,
      },
      translatedFieldTypes: [
        'string',
        { type: 'text', format: 'plain' },
        { type: 'richtext', format: 'markdown' },
        'component',
        'dynamiczone',
      ],
      translateRelations: true,
    },
  },
});

export default config;
