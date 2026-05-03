export default function Stat({ icon: Icon, label, value }) {
  return (
    <section className="stat">
      <Icon aria-hidden="true" size={18} />
      <div>
        <span>{label}</span>
        <strong>{value}</strong>
      </div>
    </section>
  );
}
