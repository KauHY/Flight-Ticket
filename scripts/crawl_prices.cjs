// Read public pages in a regular browser. No login, stealth, CAPTCHA solving or booking.
const {chromium}=require('playwright');
async function main(){
 const q=JSON.parse(process.argv[2]);
 if(!['tongcheng','spring'].includes(q.site)||!/^https:\/\/(www\.ly\.com\/flights\/itinerary\/oneway\/[A-Z]{3}-[A-Z]{3}\?date=\d{4}-\d{2}-\d{2}|flights\.ch\.com\/[A-Z]{3}-[A-Z]{3}\/)$/ .test(q.url))throw Error('查询地址无效');
 const browser=await chromium.launch({channel:'msedge',headless:true});
 try{
  const page=await browser.newPage({locale:'zh-CN'});
  const calendars=[];
  if(process.env.CRAWLER_DEBUG){page.on('pageerror',e=>console.error(e.message));page.on('requestfailed',r=>console.error(new URL(r.url()).pathname+': '+r.failure()?.errorText));}
  page.on('response',async r=>{if(r.url().startsWith('https://flights.ch.com/Flights/MinPriceTrends')){try{const v=JSON.parse((await r.text()).replace(/^\uFEFF/,''));if(String(v.Code)==='0'&&Array.isArray(v.PriceTrends))calendars.push(...v.PriceTrends);}catch(e){if(process.env.CRAWLER_DEBUG)console.error('Calendar response: '+e.message);}}});
  const response=await page.goto(q.url,{waitUntil:'domcontentloaded',timeout:30000});
  if(!response||response.status()>=400)throw Error('网站拒绝访问或暂时不可用');
  if(q.site==='tongcheng'){
   await page.waitForFunction(()=>Array.isArray(window.__NUXT__?.state?.book1?.flightLists),{},{timeout:18000});
   await page.waitForTimeout(8000);
   const rows=await page.evaluate(()=>(window.$nuxt?.$store?.state?.book1?.flightLists || window.__NUXT__.state.book1.flightLists).map(x=>Object.fromEntries(['flightNo','originAirportCode','arriveAirportCode','flyOffTime','arrivalTime','airCompanyCode','airCompanyName','flightPrice','stopNum'].map(k=>[k,x[k]]))));
   console.log(JSON.stringify({rows}));
  }else{
   await page.waitForTimeout(25000);
   if(!calendars.length)throw Error('官网未返回价格日历，可能无航线或页面结构已变化');
   console.log(JSON.stringify({calendar:calendars}));
  }
 }finally{await browser.close()}
}
main().catch(e=>{console.error('公开页面查询失败：'+e.message);process.exitCode=1});
