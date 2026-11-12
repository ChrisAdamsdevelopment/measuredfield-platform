// Derived from PBC server/growth-intelligence.mjs, evaluateGrowth + action.
// Source SHA256: 6e4217653f5ace91f238fe509a46fefc8566f3c089a2c7b6471ad57955c7e5e8
// No PBC OAuth, Google tokens, live data, private files, or network dependencies.
// Outputs are proposed actions requiring owner review, not verified outcomes.
function action(id,priority,claim,experiment,evidence,metric){
 return {id,priority,claim,experiment,evidence,metric,status:'proposed',approvalRequired:true};
}
export function evaluateGrowth({site,ai,searchConsole,days=30,businessName='This business'}){
 const displayName=typeof businessName==='string'&&businessName.trim()
   ? businessName.trim().slice(0,100) : 'This business';
 const t=site?.totals||{};
 const views=Number(t.pageViews||0),leads=Number(t.leadRequests||0);
 const pages=Array.isArray(site?.pages)?site.pages:[];
 const actions=[];
 const unbranded=(ai?.questions||[]).filter(x=>x.kind==='nonbranded');
 const testedNoMention=unbranded.filter(q=>q.results.length&&q.results.every(r=>r.businessMention===false));
 if(testedNoMention.length)actions.push(action('unbranded-discovery','high',
  displayName+' was absent from '+testedNoMention.length+' tested unbranded question(s).',
  'Improve relevant service pages with unique service scope, project evidence, clear local geography, and legitimate third-party citations. Retest identical questions.',
  testedNoMention.slice(0,7).map(x=>x.id+' '+x.prompt), 'Measured mention and citation change by question, provider and method'));
 if(views && leads===0)actions.push(action('conversion-path','medium',
  views+' consented page views and no customer inquiries in this '+days+'-day window.',
  'Audit mobile calls-to-action and form friction; measure contact clicks, form starts, completed inquiries and qualified estimates.',
  ['analytics.totals.pageViews','analytics.totals.leadRequests'], 'Inquiries per consented view (exploratory; denominator excludes non-consenting traffic)'));
 const activePages=pages.filter(p=>p.views>=2).sort((a,b)=>b.views-a.views);
 if(activePages.length)actions.push(action('high-interest-pages','medium',
  'Most-observed pages include '+activePages.slice(0,3).map(x=>x.path+' ('+x.views+' views)').join(', ')+'.',
  'Review those pages first for clear descriptions, next-step guidance, relevant internal links and engagement; avoid conclusions from tiny samples.',
  activePages.slice(0,3).map(x=>x.path), 'Page engagement and contact actions by page'));
 if(searchConsole?.status!=='connected')actions.push(action('search-console-access','high',
  'Google Search Console metrics are not yet available to this app.',
  'Authorize read-only Search Console scope and verify authorized website properties; compare impressions, clicks and search queries before changing SEO priorities.',
  [searchConsole?.reason||'No verified Google property response'], 'Actual Search Console queries, clicks and impressions for authorized website properties'));
 if((ai?.sampleCount||0)<10)actions.push(action('visibility-baseline','high',
  'Only '+(ai?.sampleCount||0)+' unique question/platform/method combinations have recorded results.',
  'Complete balanced 25-question baseline on each available service; track errors separately and retain exact response evidence.',
  ['visibility.sampleCount'], 'Completed matched prompts by platform, method and date'));
 return {windowDays:days,totals:{views,sessions:Number(t.observedSessions||0),leads,contactClicks:Number(t.contactClicks||0),
   engagedSeconds:Number(t.engagedSeconds||0),formStarts:Number(t.formStarts||0),formSubmits:Number(t.formSubmits||0)},
  topPages:activePages.slice(0,12).map(({path,views,engagedSeconds,avgEngagedSeconds,contactClicks})=>({path,views,engagedSeconds,avgEngagedSeconds,contactClicks})),
  trafficSources:(site?.sources||[]).slice(0,10),
  ai:{sampleCount:ai?.sampleCount||0,rawResults:ai?.rawResults||0,brand:ai?.brand||null,
   websites:ai?.websites||[],competitors:(ai?.competitors||[]).slice(0,10)},
  searchConsole,actions,
  methodology:'Reports are evidence-based. Browser events require consent, AI prompt results are samples, Google impressions are delayed. Correlation does not establish conversion causality. All experiments require operator review before publication or outreach.'};
}
