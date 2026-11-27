import { Box, Typography } from '@mui/material';
import { useTheme } from '@mui/material/styles';
import { brand } from '../../theme/brand';

// The Ketch mark, drawn exactly as on the landing page header: a teal tile
// of four squares, two solid and two faded.
export function BrandMark({ size = 32 }: { size?: number }) {
  const theme = useTheme();
  const tokens = brand[theme.palette.mode];
  const pad = Math.round(size * 0.22);
  return (
    <Box
      aria-hidden="true"
      sx={{
        width: size, height: size, flexShrink: 0, p: `${pad}px`, borderRadius: `${Math.round(size * 0.19)}px`,
        bgcolor: tokens.mark, display: 'grid', gridTemplateColumns: '1fr 1fr', gap: `${Math.max(2, Math.round(size / 11))}px`,
        '& span': { borderRadius: '1px', bgcolor: tokens.markText },
        '& span:nth-of-type(2), & span:nth-of-type(3)': { opacity: 0.55 },
      }}
    >
      <span /><span /><span /><span />
    </Box>
  );
}

/** "Ketch Enterprise AI", with the AI in teal, as on the landing page. */
export function BrandName({ fontSize = 14 }: { fontSize?: number }) {
  return (
    <Typography component="span" noWrap sx={{ fontSize, fontWeight: 600, letterSpacing: '-0.01em', color: 'text.primary', lineHeight: 1.2 }}>
      Ketch Enterprise <Box component="span" sx={{ color: 'primary.main' }}>AI</Box>
    </Typography>
  );
}
