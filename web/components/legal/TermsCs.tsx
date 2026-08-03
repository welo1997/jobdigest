import Link from "next/link";
import { legalHref, localeHref } from "@/i18n/config";

/**
 * Czech terms of service. A translation of `TermsEn.tsx`, section for section — see the note
 * in `PrivacyCs.tsx` about why the two copies move together, and about section 10.
 */
export default function TermsCs() {
  return (
    <>
      <div className="wrap page-head">
        <span className="label">Účinné od 20. července 2026</span>
        <h1>Podmínky služby</h1>
        <p>Smlouva mezi vámi a službou JobDigest. Přečtěte si ji prosím před přihlášením k odběru.</p>
      </div>
      <div className="wrap prose">
        <h2>1. Služba</h2>
        <p>JobDigest vám jednou denně zasílá e-mail s vybraným a seřazeným přehledem pracovních
          nabídek podle předvoleb, které zadáte. Službu provozuje Vojtěch Cizinský (Česká
          republika, EU). Přihlášením k odběru souhlasíte s těmito podmínkami a s našimi{" "}
          <Link href={legalHref("cs", "privacy")}>zásadami ochrany osobních údajů</Link>.</p>

        <h2>2. Způsobilost</h2>
        <p>Musí vám být alespoň 16 let a musíte uvést e-mailovou adresu, kterou ovládáte. K odběru
          se přihlašujete dvojím potvrzením nebo ověřením adresy přes Google — není potřeba žádný
          účet ani heslo; vše spravujete přes zabezpečený odkaz v každém e-mailu.</p>

        <h2>3. Odkud nabídky pocházejí</h2>
        <p>Nabídky shromažďujeme z veřejných zdrojů a pracovních portálů třetích stran a
          poskytujeme je pro vaše pohodlí. Řadíme je podle relevance, ale <b>nezaručujeme</b>, že
          je kterákoli nabídka přesná, aktuální, stále otevřená nebo bez chyb, a nejsme
          zaměstnavatel ani personální agentura. <b>Před podáním přihlášky si vždy ověřte údaje
          přímo na webu zaměstnavatele.</b> JobDigest není stranou žádné přihlášky, pohovoru ani
          rozhodnutí o přijetí a nenese za ně odpovědnost.</p>
        <p>Odkazujeme na původní inzerát, místo abychom ho přebírali: zobrazujeme název pozice,
          zaměstnavatele a krátké shrnutí, které píšeme sami. Mezi zdroje patří Adzuna, Remote OK,
          Remotive, Jobicy, Himalayas, We Work Remotely, Working Nomads, Arbeitnow, The Muse,
          StartupJobs a Cocuma a dále kariérní weby samotných zaměstnavatelů provozované na
          Greenhouse, Lever, Ashby, SmartRecruiters a Workday.</p>

        <h2>3a. Pro provozovatele pracovních portálů a kariérních webů</h2>
        <p>Náš robot se představuje jako <b>JobDigest/1.0</b> a respektuje soubor{" "}
          <code>robots.txt</code> včetně pravidla adresovaného tomuto jménu. Pokud si nepřejete,
          abychom vaše inzeráty načítali — z jakéhokoli důvodu a bez nutnosti ho vysvětlovat —
          napište na <b>hello@jobdigest.eu</b> a přestaneme s tím a odstraníme, co máme uloženo.
          Raději se s vámi domluvíme, než abychom byli zablokováni: máte-li API nebo feed, který
          bychom měli používat přednostně, dejte nám vědět a přejdeme na něj.</p>

        <h2>4. Přijatelné užívání</h2>
        <p>Přehled slouží k vašemu osobnímu hledání práce. Zavazujete se jeho obsah nestahovat
          automatizovaně, dále neprodávat, nepublikovat ani nešířit, nepoužívat službu protiprávně
          a nepokoušet se ji narušit ani k ní získat neoprávněný přístup. Předplatné, které službu
          nebo tyto podmínky zneužívá, můžeme pozastavit nebo ukončit.</p>

        <h2>4a. Zlepšování služby</h2>
        <p>Zaznamenáváme anonymní statistiky o používání webu bez cookies — například které
          stránky se zobrazují a kde je registrační formulář opuštěn — výhradně proto, abychom
          našli a opravili, co je matoucí nebo nefunkční. Nikdy to nezahrnuje vaši IP adresu,
          obsah vašeho životopisu ani cokoli, co vás osobně identifikuje, a respektujeme
          nastavení „Do Not Track“ ve vašem prohlížeči. Podrobnosti a způsob, jak vznést námitku,
          najdete v našich{" "}
          <Link href={legalHref("cs", "privacy")}>zásadách ochrany osobních údajů</Link>.</p>

        <h2>5. Bezplatná služba</h2>
        <p>JobDigest je aktuálně zdarma. V budoucnu můžeme zavést placené funkce; pokud se tak
          stane, váš stávající bezplatný přehled a předvolby zůstanou zachovány, pokud si sami
          nezvolíte přechod na vyšší verzi. Kteroukoli část služby můžeme kdykoli změnit,
          pozastavit nebo ukončit.</p>

        <h2>6. Bez záruk</h2>
        <p>Služba je poskytována „tak, jak je“ a „jak je dostupná“, bez jakýchkoli záruk,
          výslovných či předpokládaných, včetně vhodnosti pro určitý účel. Nezaručujeme, že služba
          bude nepřerušovaná, včasná nebo bezchybná.</p>

        <h2>7. Omezení odpovědnosti</h2>
        <p>V nejširším rozsahu povoleném zákonem neodpovídá JobDigest ani jeho provozovatel za
          nepřímé, náhodné či následné škody ani za jakoukoli ztrátu vzniklou z vašeho užívání
          služby — nebo z nemožnosti ji užívat — včetně spoléhání se na kteroukoli pracovní
          nabídku. Nic v těchto podmínkách neomezuje odpovědnost, kterou podle platného práva
          omezit nelze.</p>

        <h2>8. Změny těchto podmínek</h2>
        <p>Tyto podmínky můžeme čas od času aktualizovat. Provedeme-li podstatné změny,
          aktualizujeme datum účinnosti výše a tam, kde je to vhodné, uvědomíme odběratele
          e-mailem. Pokračujícím užíváním po změně vyjadřujete souhlas s aktualizovanými
          podmínkami.</p>

        <h2>9. Rozhodné právo</h2>
        <p>Tyto podmínky se řídí právem České republiky a Evropské unie. Máte otázky? Napište na
          <b> hello@jobdigest.eu</b>.</p>

        <h2>10. Rozhodné znění</h2>
        <p>Tento text je překladem anglického znění. V případě rozporu mezi jazykovými verzemi je
          rozhodující <Link href={legalHref("en", "terms")}>anglické znění</Link>.</p>

        <p style={{ marginTop: 24 }}>
          <Link href={localeHref("cs", "/")}>← Zpět na úvod</Link>
        </p>
      </div>
    </>
  );
}
