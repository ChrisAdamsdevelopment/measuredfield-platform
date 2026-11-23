/*
 * Optional sourced retail observations from a contractor-selected CSV.
 * Never auto-inserts products into estimates or claims local store availability.
 * RFC4180-style quotes, escaped double quotes, CRLF, UTF-8 BOM supported.
 */
const COLS=["assembly_hint","product_name","supplier","model_sku","package_qty",
"package_unit","observed_price_usd","observed_at","source_url","verified_local_store",
"price_basis","notes"];
const KEY=/^[a-z][a-z0-9_]{0,80}$/;
function csvCells(text){
 const rows=[];let row=[],cell="",quote=false;
 for(let i=0;i<text.length;i++){
  const c=text[i];
  if(quote){
   if(c==='"'&&text[i+1]==='"'){cell+='"';i++;}
   else if(c==='"')quote=false;
   else cell+=c;
  }else{
   if(c==='"')quote=true;
   else if(c===','){row.push(cell);cell="";}
   else if(c==='\n'){row.push(cell);rows.push(row);row=[];cell="";}
   else if(c==='\r'){if(text[i+1]==='\n')i++;row.push(cell);rows.push(row);row=[];cell="";}
   else cell+=c;
  }
 }
 if(quote)throw Error("CSV contains an unfinished quoted field.");
 if(row.length||cell){row.push(cell);rows.push(row);}
 return rows.filter(r=>r.some(c=>c.trim()));
}
export function parsePriceObservations(content){
 if(typeof content!=="string"||content.length>3_000_000)throw Error("CSV must be under 3 MB.");
 const records=csvCells(content.replace(/^\uFEFF/,""));
 if(records.length<2)throw Error("This price CSV has no product rows.");
 if(records.length>1001)throw Error("Limit each import to 1000 sourced products.");
 const header=records[0].map(x=>x.trim().toLowerCase());
 for(const key of COLS)if(!header.includes(key))throw Error("Missing required CSV column: "+key);
 if(new Set(header).size!==header.length)throw Error("Duplicate CSV column headings.");
 return records.slice(1).map((cells,i)=>{
  if(cells.length!==header.length)throw Error("Line "+(i+2)+" has the wrong number of CSV fields.");
  const obj=Object.fromEntries(header.map((key,j)=>[key,cells[j].trim()]));
  const price=Number(obj.observed_price_usd),qty=Number(obj.package_qty);
  if(!KEY.test(obj.assembly_hint))throw Error("Line "+(i+2)+" has an invalid assembly hint.");
  if(!obj.product_name||obj.product_name.length>220||!obj.supplier||obj.supplier.length>100)
   throw Error("Line "+(i+2)+" has a missing or overlong supplier/product.");
  if(!Number.isFinite(price)||price<=0||price>100000||!Number.isFinite(qty)||qty<=0)
   throw Error("Line "+(i+2)+" has an invalid price or package size.");
  if(!["sf","ea","gal","lf"].includes(obj.package_unit))
   throw Error("Line "+(i+2)+" has an unsupported package unit.");
  if(!/^\d{4}-\d{2}-\d{2}$/.test(obj.observed_at)||Number.isNaN(Date.parse(obj.observed_at)))
   throw Error("Line "+(i+2)+" has an invalid observation date.");
  try{const u=new URL(obj.source_url);if(u.protocol!=="https:")throw Error();}catch{
   throw Error("Line "+(i+2)+" needs an HTTPS supplier source URL.");
  }
  return {...obj,package_qty:qty,observed_price_usd:Math.round(price*100)/100};
 });
}
export function wholePackageObservationCost(line,observation){
 if(!line||!observation||line.unit!==observation.package_unit)return null;
 const qty=Number(line.qty_with_waste),packageQty=Number(observation.package_qty);
 if(!(qty>0&&packageQty>0))return null;
 const packages=Math.ceil(qty/packageQty-1e-10);
 const cents=Math.round(observation.observed_price_usd*100)*packages;
 return {packages,amount_cents:cents,
  caveat:"Minimum whole packages before offcuts, seams, system accessories, store-specific tax, delivery, availability and approval."};
}
