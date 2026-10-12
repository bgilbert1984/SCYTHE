#include <stdio.h>
#include <stdlib.h>
#include <math.h>
#include <complex.h>
#define N 4096
#define NSEG 8
static void fft(double complex*a){int n=N;for(int i=1,j=0;i<n;i++){int b=n>>1;for(;j&b;b>>=1)j^=b;j^=b;if(i<j){double complex t=a[i];a[i]=a[j];a[j]=t;}}
 for(int len=2;len<=n;len<<=1){double ang=-2*M_PI/len;double complex wl=cexp(I*ang);for(int i=0;i<n;i+=len){double complex w=1;for(int k=0;k<len/2;k++){double complex u=a[i+k],v=a[i+k+len/2]*w;a[i+k]=u+v;a[i+k+len/2]=u-v;w*=wl;}}}}
int main(int c,char**v){FILE*f=fopen(v[1],"rb");if(!f){perror("open");return 1;}
 fseek(f,0,SEEK_END);long sz=ftell(f);fseek(f,0,SEEK_SET);
 unsigned char*b=malloc(sz);if(fread(b,1,sz,f)!=sz){fprintf(stderr,"short read\n");return 1;}
 long cnt[256]={0};double si=0,sq=0,ssi=0,ssq=0;int mni=255,mxi=0,mnq=255,mxq=0;
 for(long k=0;k<sz;k++)cnt[b[k]]++;
 long ns=sz/2;for(long k=0;k<ns;k++){int i=b[2*k],q=b[2*k+1];si+=i;sq+=q;ssi+=(double)i*i;ssq+=(double)q*q;if(i<mni)mni=i;if(i>mxi)mxi=i;if(q<mnq)mnq=q;if(q>mxq)mxq=q;}
 long nseg=ns/N;static double P[NSEG][N];static double w[N];double wp=0;for(int i=0;i<N;i++){w[i]=0.5-0.5*cos(2*M_PI*i/N);wp+=w[i]*w[i];}
 static double complex a[N];long per=nseg/NSEG;
 for(int s=0;s<NSEG;s++){long a0=s*per,a1=(s==NSEG-1)?nseg:(s+1)*per;for(long g=a0;g<a1;g++){for(int i=0;i<N;i++)a[i]=w[i]*(((b[2*(g*N+i)]-127.5)+I*(b[2*(g*N+i)+1]-127.5))/127.5);fft(a);for(int i=0;i<N;i++){double complex z=a[(i+N/2)%N];P[s][i]+=creal(z)*creal(z)+cimag(z)*cimag(z);}}
  for(int i=0;i<N;i++)P[s][i]/=(double)(a1-a0)*wp;}
 printf("{\"bytes\":%ld,\"clip0\":%ld,\"clip255\":%ld,\"sumI\":%.0f,\"sumQ\":%.0f,\"sumsqI\":%.0f,\"sumsqQ\":%.0f,\"minI\":%d,\"maxI\":%d,\"minQ\":%d,\"maxQ\":%d,\"nseg\":%ld,\"P\":[",sz,cnt[0],cnt[255],si,sq,ssi,ssq,mni,mxi,mnq,mxq,nseg);
 for(int s=0;s<NSEG;s++){printf("%s[",s?",":"");for(int i=0;i<N;i++)printf("%s%.5g",i?",":"",P[s][i]);printf("]");}
 printf("]}\n");return 0;}
