/* RowReady CSV Preflight, v0.1.0 — 2026-10-12. No dependencies, no network. MIT. */
(function(root,factory){
  var api=factory();
  if(typeof module==="object"&&module.exports) module.exports=api;
  root.RowReady=api;
})(typeof globalThis!=="undefined"?globalThis:this,function(){
"use strict";
var standard=[
"URL handle","Title","Description","Vendor","Product category","Type","Tags",
"Published on online store","Status","SKU","Barcodes",
"Option1 name","Option1 value","Option1 LinkedTo","Option2 name","Option2 value","Option2 LinkedTo","Option3 name","Option3 value","Option3 LinkedTo",
"Price","Compare-at price","Cost per item","Charge tax","Inventory tracker","Inventory quantity",
"Continue selling when out of stock","Weight value (grams)","Weight unit for display",
"Packed product length","Packed product width","Packed product height","Packed product dimension unit",
"Requires shipping","Fulfillment service","Product image URL","Image position","Image alt text",
"Variant image URL","Gift card","SEO title","SEO description","Collection",
"Google Shopping / Google Product Category"
];
var aliases={
"handle":"URL handle","url handle":"URL handle","product handle":"URL handle",
"name":"Title","product name":"Title","product title":"Title",
"body (html)":"Description","product description":"Description","description (html)":"Description",
"product type":"Type","published":"Published on online store","variant sku":"SKU",
"barcode":"Barcodes","variant barcode":"Barcodes",
"option1 name":"Option1 name","option1 value":"Option1 value","option2 name":"Option2 name","option2 value":"Option2 value",
"option3 name":"Option3 name","option3 value":"Option3 value",
"variant price":"Price","variant compare at price":"Compare-at price","cost":"Cost per item",
"variant taxable":"Charge tax","variant inventory tracker":"Inventory tracker",
"variant inventory qty":"Inventory quantity","variant inventory quantity":"Inventory quantity",
"variant inventory policy":"Continue selling when out of stock",
"variant grams":"Weight value (grams)","variant weight unit":"Weight unit for display",
"variant requires shipping":"Requires shipping","variant fulfillment service":"Fulfillment service",
"image src":"Product image URL","image url":"Product image URL","image urls":"Product image URL",
"images":"Product image URL","image position":"Image position","image alt text":"Image alt text",
"variant image":"Variant image URL","variant image src":"Variant image URL",
"seo title":"SEO title","seo description":"SEO description",
"product status":"Status","price":"Price","sku":"SKU","tags":"Tags","title":"Title"
};
function canon(h){
  var s=String(h||"").trim().replace(/\s+/g," ");
  var key=s.toLowerCase();
  for(var i=0;i<standard.length;i++)if(standard[i].toLowerCase()===key)return standard[i];
  if(aliases[key])return aliases[key];
  if(/^option[123] linkedto$/i.test(s))return "Option"+s.match(/[123]/)[0]+" LinkedTo";
  if(/^(included|price|compare.at price)\s*\/\s*[^/]+$/i.test(s))return s;
  if(/product\.metafields\.[a-z0-9_]+\.[a-z0-9_]+/i.test(s))return s;
  if(/^google shopping\s*\/\s*[\w -]+$/i.test(s))return s;
  return null;
}
function parseCSV(s){
  s=String(s||"").replace(/^\uFEFF/,"");
  var rows=[], row=[], field="", quoted=false, afterQuote=false;
  for(var i=0;i<s.length;i++){
    var c=s[i];
    if(quoted){
      if(c==='"'&&s[i+1]==='"'){field+='"';i++;}
      else if(c==='"'){quoted=false;afterQuote=true;}
      else field+=c;
    }else if(afterQuote){
      if(c===','){row.push(field);field="";afterQuote=false;}
      else if(c==='\n'||c==='\r'){row.push(field);rows.push(row);row=[];field="";afterQuote=false;if(c==='\r'&&s[i+1]==='\n')i++;}
      else if(c!==' '&&c!=='\t')throw new Error("Unexpected character after closing quote at offset "+i);
    }else if(c==='"'&&field===""){quoted=true;}
    else if(c===','){row.push(field);field="";}
    else if(c==='\n'||c==='\r'){row.push(field);rows.push(row);row=[];field="";if(c==='\r'&&s[i+1]==='\n')i++;}
    else field+=c;
  }
  if(quoted)throw new Error("Unterminated quoted CSV field");
  if(afterQuote||field!==""||row.length){row.push(field);rows.push(row);}
  return rows.filter(function(r){return r.some(function(v){return v.trim()!=="";});});
}
function writeCSV(rows){
  return rows.map(function(row){return row.map(function(v){
    v=String(v===undefined||v===null?"":v);
    return /[",\r\n]/.test(v)?'"'+v.replace(/"/g,'""')+'"':v;
  }).join(",");}).join("\n")+"\n";
}
function slug(s){
  return String(s||"").normalize("NFKD").toLowerCase().replace(/[\u0300-\u036f]/g,"")
    .replace(/[^a-z0-9]+/g,"-").replace(/^-+|-+$/g,"").slice(0,220);
}
function splitImages(v){
  return String(v||"").split(/\s*[,;|]\s*(?=https?:\/\/)/i).map(function(s){return s.trim();}).filter(Boolean);
}
function repair(input){
  var parsed=parseCSV(input);
  if(parsed.length<2)throw new Error("CSV must include headers and at least one data row.");
  if(parsed.length>100001)throw new Error("Maximum 100,000 input rows per file.");
  var srcHeaders=parsed[0].map(function(s){return s.trim();});
  var issues=[], map={}, headers=[], dropped=[];
  function issue(row,severity,code,detail){issues.push({row:row,severity:severity,code:code,detail:detail});}
  srcHeaders.forEach(function(h,i){
    var target=canon(h);
    if(!target){dropped.push(h);issue(1,"warning","UNMAPPED_COLUMN","Column '"+h+"' omitted from output; verify mapping.");return;}
    if(!map[target]){map[target]=[];headers.push(target);}
    map[target].push(i);
  });
  if(!map.Title){issue(1,"error","NO_TITLE_COLUMN","Input has no recognizable Title or Name column; Shopify requires Title.");}
  if(!map["URL handle"]){headers.unshift("URL handle");}
  if(map["Product image URL"]&&!map["Image position"])headers.push("Image position");
  var results=[], handleTitles={}, skus={}, variants={}, imagesPerHandle={};
  var hadBarcode=srcHeaders.some(function(h){return h.toLowerCase()==="barcode";});
  var hadBarcodes=srcHeaders.some(function(h){return h.toLowerCase()==="barcodes";});
  if(hadBarcode&&hadBarcodes)issue(1,"error","BARCODE_HEADER_CONFLICT","Input contains both Barcode and Barcodes; remove one after comparing values.");
  function val(source,target,rowNo){
    var idx=map[target]||[];
    var found="";
    for(var k=0;k<idx.length;k++){
      var v=source[idx[k]]||"";
      if(v.trim()!==""){
        if(found&&v!==found)issue(rowNo,"warning","HEADER_COLLISION","Conflicting values mapped to "+target+"; first value kept.");
        else if(!found)found=v;
      }
    }
    return found;
  }
  for(var j=1;j<parsed.length;j++){
    var rowNo=j+1, source=parsed[j], out={};
    if(source.length>srcHeaders.length)issue(rowNo,"error","EXTRA_CELLS","More cells than headers; review delimiters.");
    headers.forEach(function(h){out[h]=val(source,h,rowNo);});
    if(!out["URL handle"]&&out.Title){out["URL handle"]=slug(out.Title);issue(rowNo,"info","GENERATED_HANDLE","Generated URL handle from title.");}
    var handle=(out["URL handle"]||"").trim();
    if(!handle)issue(rowNo,"error","MISSING_HANDLE","No URL handle or title to generate one.");
    else if(!/^[a-z0-9-]+$/.test(handle))issue(rowNo,"error","INVALID_HANDLE","Handle should use lowercase letters, numbers, hyphens only.");
    if(out.Title)handleTitles[handle]=true;
    var isVariant=!!(out.SKU||out["Option1 value"]||out["Option2 value"]||out["Option3 value"]);
    if(out.Status&&!/^(active|draft|archived)$/i.test(out.Status))issue(rowNo,"error","BAD_STATUS","Status must be active, draft or archived.");
    if(out.Price&&!/^\d+(\.\d{1,2})?$/.test(out.Price))issue(rowNo,"error","BAD_PRICE","Price must be nonnegative number with up to two decimals.");
    if(out["Inventory quantity"]&&!/^-?\d+$/.test(out["Inventory quantity"]))issue(rowNo,"error","BAD_INVENTORY","Inventory quantity must be an integer.");
    if(out["Image alt text"]&&out["Image alt text"].length>512)issue(rowNo,"warning","LONG_ALT","Image alt text exceeds 512 characters.");
    var dims=["Packed product length","Packed product width","Packed product height","Packed product dimension unit"];
    var filled=dims.filter(function(h){return !!out[h];}).length;
    if(filled>0&&filled<4&&out["Requires shipping"]!=="false")issue(rowNo,"error","PARTIAL_DIMENSIONS","All four packed product dimension fields must be populated or omitted.");
    if(isVariant&&!out["Option1 name"]&&!out["Option1 value"])issue(rowNo,"warning","MISSING_OPTION","Variant data without option columns can change existing variants when overwriting.");
    if(out.SKU){
      var sku=out.SKU.trim();
      if(skus[sku])issue(rowNo,"warning","DUPLICATE_SKU","SKU also used on input row "+skus[sku]+".");
      else skus[sku]=rowNo;
    }
    if(handle&&isVariant){
      var key=handle+"|"+[out["Option1 value"],out["Option2 value"],out["Option3 value"]].join("|");
      if(variants[key])issue(rowNo,"warning","DUPLICATE_VARIANT","Same handle and option values as input row "+variants[key]+".");
      else variants[key]=rowNo;
    }
    var urls=splitImages(out["Product image URL"]);
    if(urls.length>250)issue(rowNo,"error","TOO_MANY_IMAGES","More than 250 images in a single source cell.");
    urls.forEach(function(url){
      if(!/^https:\/\/[^\s]+$/i.test(url))issue(rowNo,"warning","IMAGE_URL","Image URL should be publicly accessible HTTPS. Not network-verified: "+url.slice(0,110));
    });
    if(out["Variant image URL"]&&!/^https:\/\/[^\s]+$/i.test(out["Variant image URL"]))issue(rowNo,"warning","VARIANT_IMAGE_URL","Variant image URL should be publicly accessible HTTPS.");
    if(handle&&urls.length){
      imagesPerHandle[handle]=(imagesPerHandle[handle]||0)+urls.length;
      if(imagesPerHandle[handle]>250)issue(rowNo,"warning","IMAGE_LIMIT","More than 250 images for this handle across rows.");
    }
    if(urls.length>1){
      out["Product image URL"]=urls[0];
      if(!out["Image position"])out["Image position"]="1";
      results.push(out);
      for(var z=1;z<urls.length;z++){
        var extra={};headers.forEach(function(h){extra[h]="";});
        extra["URL handle"]=handle;
        extra["Product image URL"]=urls[z];
        extra["Image position"]=String(z+1);
        results.push(extra);
      }
      issue(rowNo,"info","IMAGES_EXPANDED","Expanded "+urls.length+" images into separate Shopify CSV rows.");
    }else results.push(out);
  }
  Object.keys(handleTitles).forEach(function(){});
  if(!map.Title&&parsed.length>1)issue(1,"error","UNSAFE_EXPORT","No Title column; export is blocked.");
  var errors=issues.filter(function(x){return x.severity==="error";}).length;
  var output=[headers].concat(results.map(function(o){return headers.map(function(h){return o[h]||"";});}));
  var issueRows=[["Source CSV row","Severity","Code","Details"]].concat(issues.map(function(x){
    return [x.row,x.severity,x.code,x.detail.replace(/^[=+\-@]/,"'$&")];
  }));
  return {inputRows:parsed.length-1,outputRows:results.length,errors:errors,warnings:issues.filter(function(x){return x.severity==="warning";}).length,issues:issues,dropped:dropped,headers:headers,csv:errors?"":writeCSV(output),previewCSV:writeCSV(output),reportCSV:writeCSV(issueRows),safe:errors===0};
}
return {parseCSV:parseCSV,writeCSV:writeCSV,repair:repair,splitImages:splitImages,slug:slug};
});