//+------------------------------------------------------------------+
//|  JournalCollector_TEST.mq5                                       |
//|  ارسال خودکار معاملات به Journal                        |
//|  نسخه: 2.0-TEST — position-based (fix اصلی)                     |
//+------------------------------------------------------------------+
#property copyright "PriceAlert Journal"
#property version   "2.00"
#property description "ارسال خودکار معاملات به Journal"
#property strict

//===================================================================
// تنظیمات
//===================================================================
input string SERVER_URL      = "https://YOUR-APP.onrender.com";
input string API_SECRET      = "";
input int    DAYS_BACK       = 30;
input string TIMEFRAME_STR   = "1h";
input int    CANDLES_BEFORE  = 30;
input int    CANDLES_AFTER   = 30;
input bool   SEND_ON_ATTACH  = true;
input bool   LOG_VERBOSE     = true;

//===================================================================
datetime g_lastRun  = 0;
string   g_sentFile = "JC_sent_pos_live.txt";

//+------------------------------------------------------------------+
int OnInit()
  {
   Print("=== JournalCollector TEST v2.0 شروع شد ===");
   Print("Server: ", SERVER_URL, "  Days: ", DAYS_BACK, "  TF: ", TIMEFRAME_STR);
   DrawButton();
   if(SEND_ON_ATTACH) { Sleep(1500); CollectAndSend(); }
   return INIT_SUCCEEDED;
  }

void OnDeinit(const int reason)
  {
   ObjectDelete(0,"JC_SendBtn");
   ObjectDelete(0,"JC_StatusLbl");
  }

void OnTick()
  {
   if(g_lastRun > 0 && TimeCurrent() - g_lastRun > 21600)
      CollectAndSend();
  }

//+------------------------------------------------------------------+
ENUM_TIMEFRAMES StrToTF(string tf)
  {
   if(tf=="1m"||tf=="M1")  return PERIOD_M1;
   if(tf=="5m"||tf=="M5")  return PERIOD_M5;
   if(tf=="15m"||tf=="M15")return PERIOD_M15;
   if(tf=="30m"||tf=="M30")return PERIOD_M30;
   if(tf=="1h"||tf=="H1")  return PERIOD_H1;
   if(tf=="4h"||tf=="H4")  return PERIOD_H4;
   if(tf=="1d"||tf=="D1")  return PERIOD_D1;
   return PERIOD_H1;
  }

string TFToStr(ENUM_TIMEFRAMES tf)
  {
   switch(tf)
     {
      case PERIOD_M1:  return "1m";
      case PERIOD_M5:  return "5m";
      case PERIOD_M15: return "15m";
      case PERIOD_M30: return "30m";
      case PERIOD_H1:  return "1h";
      case PERIOD_H4:  return "4h";
      case PERIOD_D1:  return "1d";
      default:         return "1h";
     }
  }

string FormatTehran(datetime utc)
  {
   datetime teh = utc + 3*3600 + 30*60;
   MqlDateTime m; TimeToStruct(teh,m);
   return StringFormat("%04d-%02d-%02d %02d:%02d:%02d",m.year,m.mon,m.day,m.hour,m.min,m.sec);
  }

string EscJ(string s)
  {
   StringReplace(s,"\\","\\\\"); StringReplace(s,"\"","\\\"");
   StringReplace(s,"\n","\\n");  StringReplace(s,"\r","\\r");
   return s;
  }

string GetOutcome(double p)
  {
   if(p >  0.01) return "win";
   if(p < -0.01) return "loss";
   return "breakeven";
  }

//+------------------------------------------------------------------+
// کندل‌ها
//+------------------------------------------------------------------+
string GetCandles(string sym, ENUM_TIMEFRAMES tf, datetime entry, datetime exitt)
  {
   SymbolSelect(sym, true);
   datetime from = entry - (datetime)(CANDLES_BEFORE * PeriodSeconds(tf));
   datetime to   = exitt + (datetime)(CANDLES_AFTER  * PeriodSeconds(tf));
   if(to > TimeCurrent()) to = TimeCurrent();

   MqlRates r[];
   ArraySetAsSeries(r, false);
   int n = CopyRates(sym, tf, from, to, r);
   if(n <= 0)
     {
      Print("[EA] CopyRates 0 — sym=",sym," err=",GetLastError());
      return "[]";
     }
   Print("[EA] کندل‌ها: ", sym, " n=", n);

   string a = "[";
   for(int i=0;i<n;i++)
     {
      if(i>0) a+=",";
      a+=StringFormat("{\"t\":%d,\"o\":%.5f,\"h\":%.5f,\"l\":%.5f,\"c\":%.5f,\"v\":%d}",
                      (long)r[i].time,r[i].open,r[i].high,r[i].low,r[i].close,(long)r[i].tick_volume);
     }
   return a+"]";
  }

//+------------------------------------------------------------------+
// HTTP POST
//+------------------------------------------------------------------+
bool PostJSON(string endpoint, string body, string &resp)
  {
   string url  = SERVER_URL + endpoint;
   string hdrs = "Content-Type: application/json\r\n";
   if(StringLen(API_SECRET)>0) hdrs += "X-API-Secret: "+API_SECRET+"\r\n";

   uchar pd[], rd[]; string rh;
   StringToCharArray(body, pd, 0, StringLen(body), CP_UTF8);
   int sz=ArraySize(pd);
   if(sz>0 && pd[sz-1]==0) ArrayResize(pd,sz-1);

   ResetLastError();
   int res = WebRequest("POST", url, hdrs, 20000, pd, rd, rh);
   if(res==-1)
     {
      int e=GetLastError();
      Print("[EA] WebRequest خطا: ",e);
      if(e==4014) Print("[EA] ⚠️ URL رو در Tools>Options>Expert Advisors اضافه کن: ",SERVER_URL);
      return false;
     }
   resp = CharArrayToString(rd, 0, WHOLE_ARRAY, CP_UTF8);
   if(res!=200 && res!=201) { Print("[EA] HTTP ",res,": ",StringSubstr(resp,0,150)); return false; }
   if(LOG_VERBOSE) Print("[EA] ✅ POST OK → ",StringSubstr(resp,0,120));
   return true;
  }

//+------------------------------------------------------------------+
// چک و ذخیره position_id ارسال‌شده
//+------------------------------------------------------------------+
bool IsSent(long pid)
  {
   int h=FileOpen(g_sentFile,FILE_READ|FILE_TXT|FILE_SHARE_READ|FILE_ANSI);
   if(h==INVALID_HANDLE) return false;
   string c="";
   while(!FileIsEnding(h)) c+=FileReadString(h);
   FileClose(h);
   return StringFind(c,"|"+IntegerToString(pid)+"|")>=0;
  }

void MarkSent(long pid)
  {
   int h=FileOpen(g_sentFile,FILE_READ|FILE_WRITE|FILE_TXT|FILE_SHARE_READ|FILE_ANSI);
   if(h==INVALID_HANDLE) h=FileOpen(g_sentFile,FILE_WRITE|FILE_TXT|FILE_ANSI);
   if(h==INVALID_HANDLE) return;
   FileSeek(h,0,SEEK_END);
   FileWriteString(h,"|"+IntegerToString(pid)+"|");
   FileClose(h);
  }

//+------------------------------------------------------------------+
// ارسال یک position (جفت ورود+خروج)
// رویکرد: position_id محور — همه deal‌های یه position رو جمع میکنه
//+------------------------------------------------------------------+
bool SendPosition(long pos_id, int total_deals)
  {
   // جمع‌آوری همه deal‌های این position
   ulong  entry_ticket = 0;
   ulong  exit_ticket  = 0;
   string sym          = "";
   string direction    = "";
   double entry_price  = 0;
   double exit_price   = 0;
   datetime entry_time = 0;
   datetime exit_time  = 0;
   double total_profit = 0;
   double lots         = 0;
   string comment      = "";

   for(int i=0; i<total_deals; i++)
     {
      ulong d = HistoryDealGetTicket(i);
      if((long)HistoryDealGetInteger(d,DEAL_POSITION_ID) != pos_id) continue;

      ENUM_DEAL_TYPE  dt = (ENUM_DEAL_TYPE) HistoryDealGetInteger(d,DEAL_TYPE);
      ENUM_DEAL_ENTRY de = (ENUM_DEAL_ENTRY)HistoryDealGetInteger(d,DEAL_ENTRY);

      // فقط BUY/SELL
      if(dt!=DEAL_TYPE_BUY && dt!=DEAL_TYPE_SELL) continue;

      if(de==DEAL_ENTRY_IN)
        {
         entry_ticket = d;
         sym          = HistoryDealGetString(d,DEAL_SYMBOL);
         direction    = (dt==DEAL_TYPE_BUY)?"BUY":"SELL";
         entry_price  = HistoryDealGetDouble(d,DEAL_PRICE);
         entry_time   = (datetime)HistoryDealGetInteger(d,DEAL_TIME);
         lots         = HistoryDealGetDouble(d,DEAL_VOLUME);
         comment      = HistoryDealGetString(d,DEAL_COMMENT);
        }
      else if(de==DEAL_ENTRY_OUT || de==DEAL_ENTRY_INOUT)
        {
         exit_ticket  = d;
         exit_price   = HistoryDealGetDouble(d,DEAL_PRICE);
         exit_time    = (datetime)HistoryDealGetInteger(d,DEAL_TIME);
         total_profit += HistoryDealGetDouble(d,DEAL_PROFIT)
                       + HistoryDealGetDouble(d,DEAL_SWAP)
                       + HistoryDealGetDouble(d,DEAL_COMMISSION);
        }
     }

   // اگه ورود یا خروج پیدا نشد → باز یا ناقص
   if(entry_ticket==0 || exit_ticket==0)
     {
      if(LOG_VERBOSE)
         Print("[EA] pos=",pos_id," — ورود یا خروج پیدا نشد (احتمالاً باز)، skip");
      return false;
     }

   // SL/TP از order اول این position
   double sl_price=0, tp_price=0;
   int total_orders = HistoryOrdersTotal();
   for(int k=0;k<total_orders;k++)
     {
      ulong ord = HistoryOrderGetTicket(k);
      if((long)HistoryOrderGetInteger(ord,ORDER_POSITION_ID)!=pos_id) continue;
      ENUM_ORDER_TYPE ot=(ENUM_ORDER_TYPE)HistoryOrderGetInteger(ord,ORDER_TYPE);
      if(ot==ORDER_TYPE_BUY||ot==ORDER_TYPE_SELL)
        {
         sl_price = HistoryOrderGetDouble(ord,ORDER_SL);
         tp_price = HistoryOrderGetDouble(ord,ORDER_TP);
         break;
        }
     }

   string outcome  = GetOutcome(total_profit);
   string exit_type= (outcome=="loss")?"sl":"tp";

   // pip multiplier
   double mul=10000.0;
   string su=sym; StringToUpper(su);
   if(StringFind(su,"JPY")>=0) mul=100.0;
   if(StringFind(su,"XAU")>=0||StringFind(su,"XAG")>=0) mul=10.0;

   double sl_pips = sl_price>0 ? MathAbs(entry_price-sl_price)*mul : 0;
   double tp_pips = tp_price>0 ? MathAbs(tp_price-entry_price)*mul : 0;

   // کندل‌ها
   ENUM_TIMEFRAMES tf = StrToTF(TIMEFRAME_STR);
   string candles = GetCandles(sym, tf, entry_time, exit_time);

   // JSON
   string json="{";
   json+="\"sym\":\""         +EscJ(sym)+"\",";
   json+="\"tf\":\""          +TFToStr(tf)+"\",";
   json+="\"direction\":\""   +direction+"\",";
   json+="\"entry\":"         +DoubleToString(entry_price,5)+",";
   json+="\"exit\":"          +DoubleToString(exit_price,5)+",";
   json+="\"sl_price\":"      +(sl_price>0?DoubleToString(sl_price,5):"null")+",";
   json+="\"tp_price\":"      +(tp_price>0?DoubleToString(tp_price,5):"null")+",";
   json+="\"sl_pips\":"       +(sl_pips>0?DoubleToString(sl_pips,1):"null")+",";
   json+="\"tp_pips\":"       +(tp_pips>0?DoubleToString(tp_pips,1):"null")+",";
   json+="\"size\":"          +DoubleToString(lots,2)+",";
   json+="\"entryTime\":\""   +FormatTehran(entry_time)+"\",";
   json+="\"exitTime\":\""    +FormatTehran(exit_time)+"\",";
   json+="\"outcome\":\""     +outcome+"\",";
   json+="\"exit_type\":\""   +exit_type+"\",";
   json+="\"pnl\":"           +DoubleToString(total_profit,2)+",";
   json+="\"mt4_ticket\":"    +IntegerToString(entry_ticket)+",";
   json+="\"mt4_position_id\":"+IntegerToString(pos_id)+",";
   json+="\"mt4_profit\":"    +DoubleToString(total_profit,2)+",";
   json+="\"note\":\""        +EscJ(comment)+"\",";
   json+="\"source\":\"mt5_ea\",";
   json+="\"candle_snapshot\":"+candles;
   json+="}";

   string resp="";
   bool ok = PostJSON("/api/journal/mt4", json, resp);
   if(ok)
      Print("[EA] ✅ pos=",pos_id," ",sym," ",direction," ",outcome,
            " profit=",DoubleToString(total_profit,2)," candles=",candles=="[]"?"0":"ok");
   else
      Print("[EA] ❌ pos=",pos_id," ",sym," — ارسال ناموفق");
   return ok;
  }

//+------------------------------------------------------------------+
// تابع اصلی
//+------------------------------------------------------------------+
void CollectAndSend()
  {
   Print("=== CollectAndSend شروع — بازه:",DAYS_BACK," روز ===");
   UpdateStatus("در حال ارسال...", clrYellow);

   datetime from = TimeCurrent()-(datetime)(DAYS_BACK*86400);
   datetime to   = TimeCurrent();

   // لود کامل تاریخچه
   if(!HistorySelect(from, to))
     {
      Print("[EA] ❌ HistorySelect ناموفق");
      UpdateStatus("خطا: HistorySelect", clrRed);
      return;
     }

   int total_deals = HistoryDealsTotal();
   Print("[EA] deal در بازه: ", total_deals);

   // جمع‌آوری position_id های یکتا
   long positions[];
   int  pos_count = 0;

   for(int i=0;i<total_deals;i++)
     {
      ulong d   = HistoryDealGetTicket(i);
      long  pid = HistoryDealGetInteger(d, DEAL_POSITION_ID);
      if(pid == 0) continue;

      // یکتا بودن
      bool dup=false;
      for(int x=0;x<pos_count;x++) if(positions[x]==pid){dup=true;break;}
      if(dup) continue;

      ArrayResize(positions, pos_count+1);
      positions[pos_count++] = pid;
     }

   Print("[EA] position یکتا: ", pos_count);

   int sent=0, skip=0, fail=0;

   for(int i=0;i<pos_count;i++)
     {
      long pid = positions[i];

      if(IsSent(pid)) { skip++; continue; }

      bool ok = SendPosition(pid, total_deals);
      if(ok)        { MarkSent(pid); sent++; Sleep(500); }
      else            skip++;
     }

   string msg = StringFormat("✅ ارسال:%d | skip:%d | خطا:%d", sent, skip, fail);
   Print("=== تمام شد — ",msg," ===");
   UpdateStatus(msg, sent>0?clrLime:clrSilver);
   g_lastRun = TimeCurrent();
  }

//+------------------------------------------------------------------+
void DrawButton()
  {
   string b="JC_SendBtn";
   ObjectDelete(0,b);
   ObjectCreate(0,b,OBJ_BUTTON,0,0,0);
   ObjectSetInteger(0,b,OBJPROP_XDISTANCE,15);
   ObjectSetInteger(0,b,OBJPROP_YDISTANCE,30);
   ObjectSetInteger(0,b,OBJPROP_XSIZE,200);
   ObjectSetInteger(0,b,OBJPROP_YSIZE,36);
   ObjectSetString(0,b,OBJPROP_TEXT,"📤  ارسال به Journal");
   ObjectSetInteger(0,b,OBJPROP_COLOR,clrWhite);
   ObjectSetInteger(0,b,OBJPROP_BGCOLOR,C'40,40,55');
   ObjectSetInteger(0,b,OBJPROP_FONTSIZE,10);
   ObjectSetInteger(0,b,OBJPROP_CORNER,CORNER_LEFT_UPPER);
   ObjectSetInteger(0,b,OBJPROP_SELECTABLE,false);

   string l="JC_StatusLbl";
   ObjectDelete(0,l);
   ObjectCreate(0,l,OBJ_LABEL,0,0,0);
   ObjectSetInteger(0,l,OBJPROP_XDISTANCE,15);
   ObjectSetInteger(0,l,OBJPROP_YDISTANCE,72);
   ObjectSetString(0,l,OBJPROP_TEXT,"آماده");
   ObjectSetInteger(0,l,OBJPROP_COLOR,clrSilver);
   ObjectSetInteger(0,l,OBJPROP_FONTSIZE,9);
   ObjectSetInteger(0,l,OBJPROP_CORNER,CORNER_LEFT_UPPER);
   ObjectSetInteger(0,l,OBJPROP_SELECTABLE,false);
   ChartRedraw(0);
  }

void UpdateStatus(string msg, color clr)
  {
   ObjectSetString(0,"JC_StatusLbl",OBJPROP_TEXT,msg);
   ObjectSetInteger(0,"JC_StatusLbl",OBJPROP_COLOR,clr);
   ChartRedraw(0);
  }

void OnChartEvent(const int id,const long &lp,const double &dp,const string &sp)
  {
   if(id==CHARTEVENT_OBJECT_CLICK && sp=="JC_SendBtn")
     {
      ObjectSetInteger(0,"JC_SendBtn",OBJPROP_STATE,false);
      Print("[EA] دکمه کلیک — ارسال دستی...");
      CollectAndSend();
     }
  }
//+------------------------------------------------------------------+
