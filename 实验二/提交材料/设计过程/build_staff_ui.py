from pathlib import Path
from html import escape
out=Path('/Users/mymac/Documents/ChatGPT/软件工程/实验二/UI界面/酒店工作人员端')
B='#2563EB'; N='#142A43'; M='#64748B'; L='#E2E8F0'; BG='#F4F7FB'; G='#167D65'
class UI:
 def __init__(self):self.s=[]
 def rect(self,x,y,w,h,fill='white',r=10,stroke=None):self.s.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}" fill="{fill}"'+(f' stroke="{stroke}"' if stroke else '')+'/>')
 def text(self,x,y,t,size=14,color=N,bold=False):self.s.append(f'<text x="{x}" y="{y}" font-family="PingFang SC, Arial Unicode MS, sans-serif" font-size="{size}" font-weight="{600 if bold else 400}" fill="{color}">{escape(str(t))}</text>')
 def line(self,x,y,w):self.s.append(f'<path d="M{x} {y}h{w}" stroke="{L}"/>')
 def button(self,x,y,w,t,primary=True,disabled=False):self.rect(x,y,w,40,L if disabled else B if primary else '#EAF1FF',7);self.text(x+15,y+26,t,13,M if disabled else 'white' if primary else B,True)
 def label(self,x,y,t,fill='#EAF1FF',color=B,w=88):self.rect(x,y,w,26,fill,5);self.text(x+10,y+18,t,12,color)
 def field(self,x,y,w,label,value):self.text(x,y,label,12,M);self.rect(x,y+12,w,40,'white',6,L);self.text(x+12,y+38,value,14)
 def base(self,title,sub,active):
  self.rect(0,0,1440,1120,BG,0);self.rect(0,0,224,1120,'white',0);self.rect(224,0,1216,72,'white',0)
  self.text(30,44,'栖程 · 酒店工作台',20,B,True);self.text(256,43,'南京栖庭酒店',16,N,True);self.text(1180,43,'工作人员  李前台 ▾',14,M)
  nav=['订单处理','每日价格与库存','入住退房与结算','酒店与房型资料','实体房间状态','停售停业申请','业务统计','操作日志','消息与异常']
  for i,t in enumerate(nav):
   y=111+i*54
   if t==active:self.rect(16,y-25,192,42,'#EAF1FF',7)
   self.text(34,y,t,14,B if t==active else M,t==active)
  self.text(28,1058,'仅管理本酒店数据',12,M);self.text(28,1082,'平台审核由管理员处理',11,M)
  self.text(256,122,title,27,N,True);self.text(256,152,sub,13,M)
 def save(self,name):
  f=out/(name+'.svg');f.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="1440" height="1120" viewBox="0 0 1440 1120">'+''.join(self.s)+'</svg>');return f
u=UI();u.base('订单处理','按当前状态处理整张订单；不同房型仍属于同一次预订。','订单处理')
for i,(a,b) in enumerate([('待确认','3'),('今日到店','8'),('已入住','12'),('履约异常','1')]):
 x=256+i*290;u.rect(x,180,272,88);u.text(x+20,209,a,13,M);u.text(x+20,247,b,28,B,True)
u.rect(256,288,1152,100);u.field(276,314,270,'订单号 / 联系人','输入订单号或联系人');u.field(566,314,210,'入住日期','2026-11-02');u.field(796,314,200,'订单状态','全部状态 ▾');u.button(1030,326,150,'查询订单');u.text(1210,352,'重置',13,B)
u.rect(256,408,708,646);u.text(276,441,'订单列表',18,N,True);u.text(771,441,'共3条记录',12,M);u.line(276,460,668)
for j,(no,name,status,rooms,amount) in enumerate([('HT202610300001','张明','待确认','双床房×1 ＋ 大床房×1','¥1,320'),('HT202610300002','刘洋','已确认','标准双床房×2','¥1,200'),('HT202610290018','陈林','已入住','舒适大床房×1','¥720')]):
 y=480+j*167
 if j==0:u.rect(270,y-8,680,150,'#F0F5FF',8)
 u.text(288,y+20,no,15,N,True);u.label(827,y+1,status,w=90);u.text(288,y+51,name+'  ·  11月02日 → 11月04日 / 2晚',13,M);u.text(288,y+82,rooms,14);u.text(288,y+114,amount,20,B,True);u.text(756,y+112,'查看详情 →',13,B)
u.rect(984,408,424,646);u.text(1008,441,'当前订单 · 待确认',18,N,True);u.text(1008,476,'HT202610300001',14);u.text(1008,505,'联系人：张明  /  13900000000',13,M);u.label(1008,524,'确认截止 15:15',w=160);u.text(1008,574,'标准双床房 × 1间 · 2人',14,N,True);u.text(1008,601,'11月02日晚 ¥280 ＋ 03日晚 ¥320',12,M);u.text(1008,640,'舒适大床房 × 1间 · 2人',14,N,True);u.text(1008,667,'11月02日晚 ¥340 ＋ 03日晚 ¥380',12,M);u.line(1008,691,376);u.text(1008,727,'成交总房费  ¥1,320',22,B,True);u.text(1008,757,'已占用全部住宿晚库存；接单不重复扣减。',12,M)
u.button(1008,785,180,'确认接待整单');u.button(1200,785,184,'拒绝整单',False);u.button(1008,839,376,'查看 / 协商订单调整方案',False);u.text(1008,918,'拒绝须填写原因，并一次性释放全部占用。',12,M);u.text(1008,946,'调整须双方确认，且不延长原15分钟期限。',12,M);u.text(1008,974,'已确认订单不可修改；酒店原因取消由管理员处理。',11,M);u.text(1008,1006,'示例创建于10月30日15:00，非实时倒计时。',11,M)
u.save('工作人员端01_订单处理')

u=UI();u.base('每日价格与库存','按房型和住宿日期维护；跨日只补充新日期，不重置已有占用。','每日价格与库存')
u.rect(256,180,1152,126);u.field(280,208,270,'房型','标准双床房 ▾');u.field(574,208,300,'住宿日期范围','2026-11-02 至 2026-11-08');u.button(902,220,160,'查询');u.button(1080,220,300,'维护新进入窗口日期',False)
u.rect(256,326,1152,92,'#EAF1FF');u.text(280,358,'当前预订窗口：10月30日晚至11月13日晚',17,B,True);u.text(280,390,'价格变更仅作用于新成交；已有订单保留价格快照。维修或房量减少影响承诺时，先登记履约冲突。',13,M)
u.rect(256,438,1152,400);u.text(280,473,'标准双床房 · 分日价格与库存',19,N,True)
xs=[280,480,625,770,915,1050,1220];headers=['住宿日期','房价 / 间晚','可售总量','有效占用','可订余量','销售状态','操作']
for x,t in zip(xs,headers):u.text(x,513,t,13,M)
u.line(280,532,1104)
for i,(date,price,total,used,status) in enumerate([('11月02日',280,10,7,'可销售'),('11月03日',320,10,7,'可销售'),('11月04日',300,10,8,'可销售'),('11月05日',300,9,5,'可销售'),('11月06日',350,10,4,'停售申请中')]):
 y=568+i*52
 vals=[date,'¥'+str(price),str(total),str(used),str(0 if '停售' in status else total-used),status,'编辑']
 for k,(x,t) in enumerate(zip(xs,vals)):u.text(x,y,t,13,B if k in [4,6] else M if k==5 else N,k==4)
 u.line(280,y+17,1104)
u.rect(256,858,740,214);u.text(280,895,'编辑 11月02日晚',18,N,True);u.field(280,926,190,'新房价','¥ 280');u.field(490,926,190,'新可售总量','10');u.button(710,938,250,'核验影响并保存');u.text(280,1037,'有效占用由订单驱动，不允许手工改写；保存必须记录原因及前后值。',12,M)
u.rect(1016,858,392,214);u.text(1040,895,'销售限制与库存分开管理',16,N,True);u.text(1040,930,'有余量也可能因停售停业而不可订。',12,M);u.text(1040,961,'停售申请提交即暂停销售，须管理员审核。',12,M);u.text(1040,992,'恢复无需新增审核，但须具备解除权限。',12,M);u.text(1040,1037,'其他有效限制未解除时仍不能恢复销售。',12,M)
u.save('工作人员端02_每日价格与库存')

u=UI();u.base('入住、退房与线下结算','到店才分配实体房间；整单办理，不支持部分入住、部分退房和换房。','入住退房与结算')
u.rect(256,180,1152,116);u.field(280,207,400,'订单号 / 联系人 / 手机号','HT202610300001');u.button(704,219,160,'查询订单');u.label(900,225,'已确认',w=96);u.text(1020,251,'2间 / 4人 / 2晚',14,N,True)
u.rect(256,316,1152,112);u.text(280,354,'南京栖庭酒店  ·  张明  ·  13900000000',18,N,True);u.text(280,390,'11月02日入住 → 11月04日退房；成交总房费 ¥1,320；两种房型属于同一张订单。',14,M)
u.rect(256,448,1152,262);u.text(280,484,'入住分配方案',19,N,True);u.text(280,516,'所选房间必须符合预订房型，且当前可入住、没有未退房的冲突分配。',12,M)
for i,(room,who,number) in enumerate([('第1间 · 标准双床房','张明、李晓 / 2人','305 · 空闲可入住 ▾'),('第2间 · 舒适大床房','王晨、陈雨 / 2人','608 · 空闲可入住 ▾')]):
 y=555+i*67;u.text(280,y+21,room,15,N,True);u.text(560,y+21,who,14,M);u.rect(820,y-3,300,40,'white',6,L);u.text(834,y+23,number,14)
u.button(1160,578,220,'核验并办理整单入住')
u.rect(256,730,740,344);u.text(280,770,'线下结算记录',19,N,True);u.text(280,808,'正常履约应收 ¥1,320    净已收 ¥0    剩余应收 ¥1,320',15,B,True);u.field(280,843,190,'本次实际已收','¥ 1,320');u.field(494,843,190,'本次实际已退','¥ 0');u.field(708,843,260,'收付方式 / 凭证备注','线下现金 / 柜台收款');u.button(280,927,300,'登记实际收退款');u.text(280,1001,'登记只记录线下已发生的收付款，不执行在线支付。',13,M);u.text(280,1030,'费用更正保留前后历史；同一业务事件不重复记账。',12,M)
u.rect(1016,730,392,344);u.text(1040,770,'整单退房',19,N,True);u.text(1040,811,'当前订单尚未入住，暂不可办理退房。',13,M);u.button(1040,840,344,'办理整单退房',False,True);u.text(1040,920,'办理入住后，退房入口才可用。',13,M);u.text(1040,953,'退房时保留分配历史，房间进入待清洁。',12,M);u.text(1040,986,'清洁完成后恢复可入住，不额外增加退房日库存。',11,M);u.text(1040,1030,'只有已确认订单可以办理入住。',12,G)
u.save('工作人员端03_入住退房与结算')
print(out)
