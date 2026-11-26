import { writeFile } from 'node:fs/promises';
import { securityHeaders } from '../security/headers.js';

const content = '# Generated from security/headers.js; applies to static responses.\n/*\n'
  + Object.entries(securityHeaders).map(([name, value]) => `  ${name}: ${value}`).join('\n')
  + '\n';
await writeFile(new URL('../public/_headers', import.meta.url), content);
