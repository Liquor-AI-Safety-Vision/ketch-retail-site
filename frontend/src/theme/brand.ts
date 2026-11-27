// Ketch brand tokens for the dashboard (MUI needs concrete colours for its
// alpha() helpers). They mirror the --ketch-* and --dashboard-* variables in
// src/index.css that the landing page uses: change a colour in both places.

export const brand = {
  name: 'Ketch Enterprise AI',
  tagline: 'Retail loss prevention',
  fontFamily: '"Outfit Variable", "Outfit", system-ui, -apple-system, "Segoe UI", sans-serif',
  light: {
    primary: '#0B6B69', // --ketch-teal-700: AA on cream for text and fills
    primaryLight: '#128C8A', // --ketch-teal-600
    primaryDark: '#0E5C5B', // --ketch-teal-900
    canvas: '#F5F2EB', // --dashboard-canvas
    paper: '#FFFCF7', // --dashboard-paper
    sidebar: '#ECE8DF', // --dashboard-sidebar
    sidebarActive: '#DDD7CB',
    sidebarHover: '#E5E0D6',
    divider: '#D5D0C6', // --dashboard-border
    text: '#15211F', // --ketch-ink
    textSecondary: '#3A4744', // --ketch-ink-600
    textDisabled: '#6E7672',
    mark: '#0E5C5B',
    markText: '#F6F1E7',
  },
  dark: {
    primary: '#55B0AB', // --ketch-teal-700 (dark)
    primaryLight: '#78C7C2',
    primaryDark: '#2E7773',
    canvas: '#0C0E0F',
    paper: '#14191A',
    sidebar: '#101415',
    sidebarActive: '#1B211F',
    sidebarHover: '#161B19',
    divider: '#282E2D',
    text: '#F1EEE7', // --ketch-ink (dark)
    textSecondary: '#ADB6B2', // --ketch-ink-600 (dark)
    textDisabled: '#7D8581',
    mark: '#55B0AB',
    markText: '#0C0E0F',
  },
} as const;

export type BrandMode = keyof Pick<typeof brand, 'light' | 'dark'>;
