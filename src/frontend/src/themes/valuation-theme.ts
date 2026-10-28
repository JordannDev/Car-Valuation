import { defineTheme } from '@astryxdesign/core/theme';
import { themes } from '../astryx-themes';

export const valuationTheme = defineTheme({
  name: 'valuation',
  extends: themes.neutral,
  tokens: {
    '--color-accent': ['oklch(0.54 0.2 255)', 'oklch(0.73 0.15 255)'],
    '--color-background-body': ['oklch(0.982 0.008 250)', 'oklch(0.15 0.01 250)'],
    '--color-text-green': ['oklch(0.56 0.17 145)', 'oklch(0.78 0.16 145)'],
  },
});
