/* VendorSettle Desk — local-only consignment settlement engine.
   No network, cookies, or external dependencies. Copyright (c) 2026.
   Reports are informational; no transfers or tax filings are performed. */
(function(root) {
  "use strict";
  const aliases = {
    vendor:["vendor","vendor_name","consignor","consignor_name","seller","seller_name","vendor_id"],
    gross:["gross","gross_sale","gross_sales","sale_amount","amount","price","total"],
    date:["date","sale_date","sold_at","transaction_date"],
    item:["item","item_name","description","product","title","sku"],
    txid:["transaction_id","sale_id","order_id","receipt_id","id"],
    refund:["refund","refunds","returned_amount","refund_amount"],
    fee:["fee","fees","processing_fee","deductions"],
    rate:["vendor_rate","vendor_share_pct","consignor_rate","split_pct","vendor_percent"]
  };
  function parseCSV(input) {
    const s=String(input).replace(/^\uFEFF/,""); const rows=[]; let row=[],field="",quoted=false;
    for(let i=0;i<s.length;i++){
      const c=s[i];
      if(quoted){
        if(c==='"'&&s[i+1]==='"'){field+='"';i++;}
        else if(c==='"'){quoted=false;}
        else field+=c;
      } else if(c==='"'){if(field.trim()!=="")throw Error("Unexpected quote at character "+(i+1));quoted=true;}
      else if(c===","){row.push(field);field="";}
      else if(c==="\n"||c==="\r"){if(c==="\r"&&s[i+1]==="\n")i++;row.push(field);field="";if(row.some(x=>x.trim()!==""))rows.push(row);row=[];}
      else field+=c;
    }
    if(quoted)throw Error("Unclosed quoted CSV field");
    row.push(field);if(row.some(x=>x.trim()!==""))rows.push(row);
    if(rows.length<2)throw Error("CSV requires a header and at least one data row");
    return rows;
  }
  function cents(s,label,row) {
    let x=String(s??"").trim();if(x==="")return 0;
    let neg=false;if(/^\(.*\)$/.test(x)){neg=true;x=x.slice(1,-1);}
    x=x.replace(/[$,\s]/g,"");
    if(!/^-?\d+(\.\d{1,2})?$/.test(x))throw Error("Row "+row+": invalid "+label+" amount: "+String(s));
    if(x[0]==="-"){neg=!neg;x=x.slice(1);}
    const parts=x.split(".");const n=Number(parts[0])*100+Number((parts[1]||"").padEnd(2,"0"));
    if(!Number.isSafeInteger(n)||n>1e12)throw Error("Row "+row+": "+label+" is too large");
    return neg?-n:n;
  }
  function rate(s,defaultRate,row) {
    if(String(s??"").trim()==="")return defaultRate;
    let x=String(s).trim(),percent=x.endsWith("%");if(percent)x=x.slice(0,-1).trim();
    if(!/^\d+(\.\d{1,4})?$/.test(x))throw Error("Row "+row+": invalid vendor rate");
    let n=Number(x);if(percent||n>1)n=n/100;
    if(n<0||n>1)throw Error("Row "+row+": vendor rate must be 0–100%");
    return n;
  }
  function fmt(n){return (n/100).toFixed(2);}
  function safe(v){let s=String(v??"");if(/^[\s]*[=+\-@\t\r]/.test(s))s="'"+s;return /[",\r\n]/.test(s)?'"'+s.replace(/"/g,'""')+'"':s;}
  function csv(rows){return rows.map(r=>r.map(safe).join(",")).join("\r\n")+"\r\n";}
  function key(s){return String(s).trim().toLowerCase().replace(/[\s-]+/g,"_");}
  function col(headers,field){const opts=aliases[field];return headers.findIndex(h=>opts.includes(key(h)));}
  function settle(text,opts) {
    opts=opts||{};const def=rate(opts.defaultRate??"60%",.6,0);
    const rows=parseCSV(text),headers=rows.shift(),idx={};
    for(const f of Object.keys(aliases))idx[f]=col(headers,f);
    if(idx.vendor<0||idx.gross<0)throw Error("CSV must have vendor (or consignor) and gross (or sale_amount) columns");
    const get=(r,k)=>idx[k]<0?"":(r[idx[k]]??"");
    const warnings=[],details=[],groups=new Map(),seen=new Set();
    for(let i=0;i<rows.length;i++){
      const r=rows[i],line=i+2;
      if(r.length>headers.length&&r.slice(headers.length).some(x=>x.trim()!==""))throw Error("Row "+line+": more values than headers");
      const vendor=String(get(r,"vendor")).trim();if(!vendor)throw Error("Row "+line+": vendor is empty");
      const date=String(get(r,"date")).trim();
      if(date&&!/^\d{4}-\d{2}-\d{2}$/.test(date))warnings.push("Row "+line+": non-ISO date; date filtering may skip it");
      if(opts.from&&date&&date<opts.from)continue;
      if(opts.to&&date&&date>opts.to)continue;
      const gross=cents(get(r,"gross"),"gross",line),refund=cents(get(r,"refund"),"refund",line),fee=cents(get(r,"fee"),"fee",line);
      if(refund<0||fee<0)throw Error("Row "+line+": refunds and fees must be non-negative");
      if(refund>Math.max(0,gross))warnings.push("Row "+line+": refunds exceed gross");
      const net=gross-refund-fee,share=rate(get(r,"rate"),def,line);
      const payout=Math.round(net*share),retained=net-payout;
      const d={line,vendor,date,item:String(get(r,"item")).trim(),txid:String(get(r,"txid")).trim(),gross,refund,fee,net,share,payout,retained};
      const fingerprint=[d.vendor,d.date,d.item,d.txid,d.gross,d.refund,d.fee,d.share].join("\u001f");
      if(seen.has(fingerprint))warnings.push("Row "+line+": possible duplicate transaction line");seen.add(fingerprint);
      details.push(d);
      if(!groups.has(vendor))groups.set(vendor,{vendor,lines:0,gross:0,refund:0,fee:0,net:0,payout:0,retained:0});
      const g=groups.get(vendor);g.lines++;for(const k of ["gross","refund","fee","net","payout","retained"])g[k]+=d[k];
    }
    if(!details.length)throw Error("No transactions remain after filtering");
    const vendors=[...groups.values()].sort((a,b)=>a.vendor.localeCompare(b.vendor));
    for(const g of vendors)if(g.payout<0)warnings.push("Vendor "+g.vendor+" has a negative balance: review before paying");
    const totals={vendors:vendors.length,lines:details.length,gross:0,refund:0,fee:0,net:0,payout:0,retained:0};
    for(const g of vendors)for(const k of ["gross","refund","fee","net","payout","retained"])totals[k]+=g[k];
    const payoutCSV=csv([["Vendor","Line items","Gross sales","Refunds","Fees","Net distributable","Vendor payout","Store retained"],...vendors.map(g=>[g.vendor,g.lines,...["gross","refund","fee","net","payout","retained"].map(k=>fmt(g[k]))])]);
    const detailCSV=csv([["CSV row","Vendor","Date","Transaction ID","Item","Gross","Refund","Fee","Net","Vendor rate %","Vendor payout","Store retained"],...details.map(d=>[d.line,d.vendor,d.date,d.txid,d.item,...["gross","refund","fee","net"].map(k=>fmt(d[k])),(d.share*100).toFixed(2),fmt(d.payout),fmt(d.retained)])]);
    return {vendors,details,totals,warnings,payoutCSV,detailCSV,fmt};
  }
  root.VendorSettle={settle,parseCSV,fmt};
})(typeof window!=="undefined"?window:globalThis);
