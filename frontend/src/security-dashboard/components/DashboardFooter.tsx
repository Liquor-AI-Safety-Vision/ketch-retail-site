import { Box, Divider, Typography } from '@mui/material';

export function DashboardFooter() {
  return (
    <Box
      component="footer"
      sx={{
        flexShrink: 0,
        px: { xs: 2, sm: 2.5 },
        pb: { xs: 2.25, sm: 3 },
      }}
    >
      <Box
        sx={{
          maxWidth: 1720,
          mx: 'auto',
          pt: 2.25,
          borderTop: '1px solid',
          borderColor: 'divider',
          display: 'flex',
          flexDirection: { xs: 'column', sm: 'row' },
          alignItems: { xs: 'flex-start', sm: 'center' },
          justifyContent: 'space-between',
          gap: 1.5,
        }}
      >
        {/* The brand sits at the top of the sidebar; the footer only says
            where the video lives. */}
        <Typography sx={{ color: 'text.secondary', fontSize: 12 }}>Ketch Enterprise AI · Retail loss prevention</Typography>
        <Box
          sx={{
            display: 'flex',
            flexWrap: 'wrap',
            alignItems: 'center',
            gap: { xs: 0.9, sm: 1.25 },
            color: 'text.secondary',
          }}
        >
          <Typography sx={{ fontSize: 12 }}>Runs fully on-prem.</Typography>
          <Divider orientation="vertical" flexItem sx={{ display: { xs: 'none', sm: 'block' } }} />
          <Typography sx={{ fontSize: 12 }}>Video stays in your building.</Typography>
          <Divider orientation="vertical" flexItem sx={{ display: { xs: 'none', md: 'block' } }} />
          <Typography sx={{ width: { xs: '100%', md: 'auto' }, fontSize: 12, color: 'text.secondary' }}>
            © 2026 Ketch Enterprise AI
          </Typography>
        </Box>
      </Box>
    </Box>
  );
}
