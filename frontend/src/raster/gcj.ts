// Amap tiles use GCJ-02 in mainland China; API and inspector remain WGS84.
const a = 6378245, ee = 0.006693421622965943;
const outside = (lng: number, lat: number) => lng < 72.004 || lng > 137.8347 || lat < 0.8293 || lat > 55.8271;
export function wgsToGcj(lng: number, lat: number): [number, number] {
  if (outside(lng, lat)) return [lng, lat];
  const x = lng - 105, y = lat - 35;
  let dlat = -100 + 2*x + 3*y + .2*y*y + .1*x*y + .2*Math.sqrt(Math.abs(x));
  dlat += (20*Math.sin(6*x*Math.PI)+20*Math.sin(2*x*Math.PI))*2/3;
  dlat += (20*Math.sin(y*Math.PI)+40*Math.sin(y/3*Math.PI))*2/3;
  dlat += (160*Math.sin(y/12*Math.PI)+320*Math.sin(y*Math.PI/30))*2/3;
  let dlng = 300+x+2*y+.1*x*x+.1*x*y+.1*Math.sqrt(Math.abs(x));
  dlng += (20*Math.sin(6*x*Math.PI)+20*Math.sin(2*x*Math.PI))*2/3;
  dlng += (20*Math.sin(x*Math.PI)+40*Math.sin(x/3*Math.PI))*2/3;
  dlng += (150*Math.sin(x/12*Math.PI)+300*Math.sin(x/30*Math.PI))*2/3;
  const rad = lat / 180 * Math.PI, magic = 1-ee*Math.sin(rad)**2, root = Math.sqrt(magic);
  return [lng + dlng * 180 / (a/root*Math.cos(rad)*Math.PI), lat + dlat * 180 / ((a*(1-ee))/(magic*root)*Math.PI)];
}
export function gcjToWgs(lng: number, lat: number): [number, number] {
  if (outside(lng, lat)) return [lng, lat];
  let x = lng, y = lat;
  for (let i=0; i<6; i++) { const [u,v] = wgsToGcj(x,y); x -= u-lng; y -= v-lat; }
  return [x,y];
}
