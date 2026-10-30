const sharp=require('/Users/mymac/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/sharp');
const fs=require('fs'),path=require('path');
const root='/Users/mymac/Documents/ChatGPT/软件工程/实验二/UI界面';
function walk(p){return fs.readdirSync(p,{withFileTypes:true}).flatMap(e=>e.isDirectory()?walk(path.join(p,e.name)):[path.join(p,e.name)]);}
(async()=>{for(const file of walk(root).filter(n=>n.endsWith('.svg'))){await sharp(file,{density:144}).png().toFile(file.replace('.svg','.png'));console.log(path.basename(file));}})();
