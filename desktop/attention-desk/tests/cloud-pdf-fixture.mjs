// Deliberately synthetic PDF, kept entirely in isolated browser test storage.
export function pdfFixture(){
  const objects=[];
  objects.push('<< /Type /Catalog /Pages 2 0 R >>','<< /Type /Pages /Count 3 /Kids [3 0 R 5 0 R 7 0 R] >>');
  for(let page=1;page<=3;page++){
    const content=`0.95 g 0 0 600 840 re f 0 g BT /F1 24 Tf 50 740 Td (Synthetic source page ${page}) Tj ET 0.2 0.35 0.7 rg 50 600 500 60 re f`;
    objects.push(`<< /Type /Page /Parent 2 0 R /MediaBox [0 0 600 840] /Resources << /Font << /F1 9 0 R >> >> /Contents ${page*2+2} 0 R >>`,`<< /Length ${Buffer.byteLength(content)} >>\nstream\n${content}\nendstream`);
  }
  objects.push('<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>');
  let text='%PDF-1.7\n',offsets=[0];
  for(let index=0;index<objects.length;index++){offsets.push(Buffer.byteLength(text));text+=`${index+1} 0 obj\n${objects[index]}\nendobj\n`;}
  text+='%'+'.'.repeat(1_200_000)+'\n';
  const start=Buffer.byteLength(text);text+=`xref\n0 ${objects.length+1}\n0000000000 65535 f \n`;
  for(const offset of offsets.slice(1))text+=String(offset).padStart(10,'0')+' 00000 n \n';
  text+=`trailer\n<< /Size ${objects.length+1} /Root 1 0 R >>\nstartxref\n${start}\n%%EOF\n`;
  return Buffer.from(text);
}
