"use client";
import { Badge } from "./ui";

export default function StockPanel({ s }: { s: any }) {
  const opt = (o: any) => (
    <tr key={`${o.pharmacy_id}-${o.inventory_id}`}>
      <td>{o.pharmacy} <small className="muted">{o.distance_miles} mi</small></td>
      <td>{o.brand}{o.generic && <> <Badge tone="ok">generic</Badge></>}</td>
      <td>{o.ingredient} {o.strength_mg} mg</td>
      <td>{o.qty}</td>
      <td>₹{o.unit_price}</td>
    </tr>
  );
  return (
    <div>
      <h3>Stock (answered by {s.answered_by}, radius {s.radius_miles} mi)</h3>
      {s.on_site_in_stock ? <div className="okbox">In stock at your hospital pharmacy.</div> : <div className="warnbox">Not in stock on site.</div>}
      <table>
        <thead><tr><th>Pharmacy</th><th>Product</th><th>Molecule</th><th>Qty</th><th>Price</th></tr></thead>
        <tbody>
          {s.in_radius?.map(opt)}
          {s.bioequivalents?.length > 0 && <tr><td colSpan={5}><b>Bio-equivalent alternatives</b></td></tr>}
          {s.bioequivalents?.map(opt)}
          {s.same_class_alternatives?.length > 0 && <tr><td colSpan={5}><b>Same-class alternatives (prescriber decision)</b></td></tr>}
          {s.same_class_alternatives?.map(opt)}
        </tbody>
      </table>
      {s.nearest_outside_radius && <p className="muted">Nearest outside radius: {s.nearest_outside_radius.pharmacy} at {s.nearest_outside_radius.distance_miles} mi.</p>}
    </div>
  );
}
