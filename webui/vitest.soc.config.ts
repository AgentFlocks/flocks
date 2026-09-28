// Bundled SOC pages live outside webui/, so resolve their React imports here.
import { mergeConfig } from 'vitest/config';
import path from 'path';
import base from './vitest.config';
export default mergeConfig(base, {
  resolve: {
    alias: {
      'react': path.resolve(__dirname, 'node_modules/react'),
      '@flocks/webui-contract-sdk': path.resolve(__dirname, 'src/test/mocks/socContractSdk.ts'),
    },
  },
});
