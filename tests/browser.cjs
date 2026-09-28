const {chromium}=require('playwright');
(async()=>{
 const b=await chromium.launch({headless:true,channel:'msedge'});
 const p=await b.newPage({viewport:{width:1440,height:1000}});
 const errors=[];p.on('pageerror',e=>errors.push(e.message));
 await p.goto('http://127.0.0.1:8765');
 await p.getByRole('button',{name:'＋ 新建监控'}).click();
 await p.locator('#monitor-form [name=origin]').fill('SHA');await p.locator('#monitor-form [name=destination]').fill('CAN');
 const day=new Date(Date.now()+86400000*7).toISOString().slice(0,10);
 await p.locator('#monitor-form [name=date_from]').fill(day);await p.locator('[name=target]').fill('650');
 await p.locator('[name=email]').fill('demo@example.com');await p.locator('#monitor-form [name=demo]').check();
 const createdResponse=p.waitForResponse(r=>r.url().endsWith('/api/monitors')&&r.request().method()==='POST');
 await p.getByRole('button',{name:'创建并检查票价'}).click();
 const createdMonitor=await(await createdResponse).json();
 await p.locator(`[data-detail="${createdMonitor.id}"]`).click();
 try { await p.getByText('比同日直飞省 ¥100').waitFor(); }
 catch(e){await p.screenshot({path:'test-results-failure.png',fullPage:true});console.log('Page errors:',errors);console.log((await p.locator('body').innerText()).slice(-5000));throw e;}
 await p.screenshot({path:'test-results-desktop.png',fullPage:true});
 await p.setViewportSize({width:390,height:844});await p.screenshot({path:'test-results-mobile.png',fullPage:true});
 if(await p.evaluate(()=>document.documentElement.scrollWidth>innerWidth))throw Error('Mobile overflow');
 await p.getByRole('button',{name:'⚙　数据与邮件'}).click();await p.getByRole('heading',{name:'连接你的数据源'}).waitFor();
 await p.getByRole('heading',{name:'飞猪 FlyAI',exact:true}).waitFor();
 if(await p.locator('.provider[data-kind="partner"] .p-enabled:disabled').count()<15)throw Error('Partner source catalog incomplete');
 for(const kind of ['tongcheng','spring','variflight']){
  const row=p.locator(`.provider[data-kind="${kind}"]`);
  if(await row.count()!==1||await row.locator('.p-token,.p-endpoint').count())throw Error('Crawler source configuration incorrect');
 }
 await p.getByText('已内置 2026-08-05 起成人国内标准',{exact:false}).waitFor();
 await p.getByRole('button',{name:'保存设置',exact:true}).click();
 await p.getByText('配置已保存',{exact:true}).waitFor();
 await p.screenshot({path:'test-results-providers.png',fullPage:true});
 await p.getByRole('button',{name:'✈　航班比价'}).click();
 await p.locator('#flight-form [name=origin]').fill('上海');
 await p.getByRole('option').filter({hasText:'上海 · 全部机场'}).click();
 await p.locator('#flight-form [name=destination]').fill('CAN');
 await p.locator('#flight-form [name=date_from]').fill(day);
 await p.locator('#flight-form [name=demo]').check();
 await p.getByRole('button',{name:'查询各航班最低价'}).click();
 await p.getByText('2 个航班',{exact:true}).waitFor();
 if(await p.locator('.flight-table tbody tr').count()!==2)throw Error('City flight board should contain both airports');
 if(await p.evaluate(()=>document.documentElement.scrollWidth>innerWidth))throw Error('Mobile flight board overflow');
 await p.screenshot({path:'test-results-flights-mobile.png',fullPage:true});
 await p.setViewportSize({width:1440,height:1000});await p.screenshot({path:'test-results-flights.png',fullPage:true});
 await p.locator('#board-filter').fill('PVG');
 if(await p.locator('.flight-table tbody tr').count()!==1)throw Error('Airport filter failed');
 await p.locator('#board-filter').fill('NO-SUCH-FLIGHT');await p.getByRole('heading',{name:'暂无匹配航班'}).waitFor();
 await p.getByRole('button',{name:'⌕　全球机场'}).click();
 await p.locator('#directory-search').fill('ZBAA');
 await p.locator('#directory-results').getByText('北京首都',{exact:true}).waitFor();
 if(!(await p.locator('#directory-stats').innerText()).includes('28,298'))throw Error('Airport dataset missing');
 await p.screenshot({path:'test-results-airports.png',fullPage:true});
 if(errors.length)throw Error(errors.join('\n'));
 const state=await(await p.request.get('http://127.0.0.1:8765/api/state')).json();
 const created=state.monitors.find(m=>m.id===createdMonitor.id);
 if(created)await p.request.post('http://127.0.0.1:8765/api/action',{data:{id:created.id,action:'delete'}});
 console.log('PASS: demo creation, tax-inclusive comparison, desktop/mobile layout, settings, no JS errors');
 await b.close();
})().catch(e=>{console.error(e);process.exit(1)});
