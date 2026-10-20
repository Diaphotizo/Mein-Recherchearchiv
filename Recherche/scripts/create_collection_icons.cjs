/* Build Windows icons directly from the corresponding vector assets. */
const fs = require('node:fs/promises');
const path = require('node:path');
const sharp = require('sharp');
const web = path.resolve(__dirname, '..', 'web');
const sizes = [16, 24, 32, 48, 64, 128, 256];

async function icon(svgPath, icoPath) {
  const buffers = await Promise.all(sizes.map(size =>
    sharp(svgPath, { density: 384 }).resize(size, size).png().toBuffer()));
  const header = Buffer.alloc(6 + 16 * sizes.length);
  header.writeUInt16LE(1, 2);
  header.writeUInt16LE(sizes.length, 4);
  let offset = header.length;
  for (let i = 0; i < sizes.length; i++) {
    const entry = 6 + 16 * i;
    header[entry] = sizes[i] === 256 ? 0 : sizes[i];
    header[entry + 1] = header[entry];
    header.writeUInt16LE(1, entry + 4);
    header.writeUInt16LE(32, entry + 6);
    header.writeUInt32LE(buffers[i].length, entry + 8);
    header.writeUInt32LE(offset, entry + 12);
    offset += buffers[i].length;
  }
  await fs.writeFile(icoPath, Buffer.concat([header, ...buffers]));
}
const bipWeb = path.resolve(__dirname, '..', '..', 'BIP', 'recherche', 'web');
Promise.all([
  ...['archive', 'courts', 'un'].map(name => icon(path.join(web, name + '.svg'), path.join(web, name + '.ico'))),
  icon(path.join(bipWeb, 'favicon.svg'), path.join(bipWeb, 'bip.ico'))
])
  .then(() => console.log('Archiv-, Gerichts-, BIP- und UN-Symbole erstellt.'))
  .catch(error => { console.error(error); process.exitCode = 1; });
