import Link from "next/link";
import { legalHref, localeHref } from "@/i18n/config";

/**
 * Czech privacy policy.
 *
 * A translation of `PrivacyEn.tsx`, section for section and promise for promise. The two must
 * be changed in the same commit — CLAUDE.md treats this document as a specification, and a
 * translation that lags is not a cosmetic problem but a second, false statement of what the
 * product does with someone's data.
 *
 * Section 11 names English as authoritative. That is not a licence to let this copy drift: it
 * is the standard safeguard for the window between a change landing and being noticed, and it
 * tells a Czech reader which text governs if the two ever disagree.
 */
export default function PrivacyCs() {
  return (
    <>
      <div className="wrap page-head">
        <span className="label">Účinné od 13. srpna 2026</span>
        <h1>Zásady ochrany osobních údajů</h1>
        <p>Jak JobDigest shromažďuje, používá a chrání vaše údaje. Srozumitelně a bez překvapení.</p>
      </div>
      <div className="wrap prose">
        <h2>1. Kdo jsme</h2>
        <p>JobDigest („my“) je služba zasílající denní e-mailový přehled pracovních nabídek,
          kterou provozuje Vojtěch Cizinský se sídlem v České republice (EU). Jsme správcem
          osobních údajů popsaných níže. Kdykoli nás kontaktujte na <b>hello@jobdigest.eu</b>;
          poštovní adresu sdělíme na vyžádání.</p>

        <h2>2. Co shromažďujeme</h2>
        <p>Záměrně shromažďujeme co nejméně údajů:</p>
        <ul>
          <li><b>Vaši e-mailovou adresu</b> — abychom vám mohli poslat přehled a potvrzení.</li>
          <li><b>Vaše vyhledávací předvolby</b> — pozice, dovednosti a klíčová slova, země a
            města, kde chcete pracovat, jaké formy práce přijmete (z kanceláře, hybridně, plně
            na dálku), jaké požadavky na vzdělání přijmete a — pokud jej uvedete — co jste
            studovali, obory, které nám uvedete jako zajímavé, jak daleko může být plně vzdálená
            pozice, typ práce a frekvenci, tak jak
            je zadáte, a jazyk, ve kterém web čtete — aby vám v něm chodily i e-maily.</li>
          <li><b>Signály odvozené z životopisu</b> — pokud životopis nahrajete (nepovinné), viz
            oddíl 4.</li>
          <li><b>Záznamy o doručení</b> — které nabídky jsme vám již poslali, abychom stejnou
            nabídku nikdy neposlali dvakrát, a časové značky odeslání a potvrzení.</li>
          <li><b>Nabídky, které skryjete</b> — když nabídku na stránce se svými nabídkami
            skryjete (protože jste se už přihlásili nebo pro vás není), uložíme si tuto volbu
            k vašemu odběru, abychom ji přestali zobrazovat i posílat e-mailem. Všechny je
            vidíte na stránce se skrytými nabídkami a můžete je kdykoli vrátit zpět.</li>
          <li><b>Statistiky používání šetrné k soukromí</b> — které stránky se zobrazují a které
            kroky registračního formuláře jsou dosaženy nebo selžou, abychom mohli opravit, co je
            matoucí. Vás neidentifikují; viz oddíl 6.</li>
        </ul>
        <p><b>Neshromažďujeme</b> jména, telefonní čísla ani platební údaje a nesledujeme vás
          napříč jinými weby. Osobní údaje nekupujeme ani neprodáváme.</p>

        <h2>3. Právní základ a jak údaje používáme</h2>
        <p>Vaše údaje zpracováváme na základě vašeho <b>souhlasu</b> — uděleného buď dvojím
          potvrzením (kliknete na odkaz, který vám pošleme e-mailem), nebo, pokud se
          zaregistrujete přes Google, tím, že Google ověří, že adresu ovládáte — abychom vám
          mohli zasílat denní přehled nabídek a provozní e-maily nutné k jeho správě. Souhlas
          můžete kdykoli odvolat odhlášením — jedním kliknutím z kteréhokoli e-mailu — což
          okamžitě zastaví veškeré zasílání.</p>
        <p>Statistiky používání podle oddílu 6 zpracováváme na základě našeho
          <b> oprávněného zájmu</b> (čl. 6 odst. 1 písm. f) GDPR) porozumět tomu, které části
          našeho vlastního webu jsou matoucí nebo nefunkční, abychom je mohli zlepšit. Jsou
          navrženy tak, aby vás neidentifikovaly — bez cookies, bez IP adres a bez sledování
          napříč weby — takže dopad na vaše soukromí je minimální, proti zpracování však můžete
          kdykoli vznést námitku
          (oddíl 8) a nastavení „Do Not Track“ a Global Privacy Control ve vašem prohlížeči
          respektujeme automaticky.</p>

        <h2>4. Váš životopis — přečíst a zahodit</h2>
        <p>Pokud se rozhodnete nahrát životopis, přečteme jej <b>jednou, v paměti</b>, abychom
          rozpoznali relevantní dovednosti, pozice a vaše nejvyšší dosažené vzdělání, a poté
          soubor <b>okamžitě smažeme</b>. Samotný dokument nikdy neukládáme a nikdy jej
          nepředáváme žádné třetí straně. Uloží se pouze krátké odvozené shrnutí — například
          „Rozpoznáno: datové inženýrství, dbt, Snowflake · ~4 roky · magistr ekonomie“.
          Rozpoznané vzdělání slouží jen k předvyplnění formuláře, kde jej před odesláním
          můžete změnit.</p>
        <p>Toto shrnutí je součástí vašeho profilu pro párování, takže je spolu s ostatními
          předvolbami zpracováno naším poskytovatelem párování (oddíl 5). Samotný dokument a
          jakýkoli detail v něm, který jsme neshrnuli — zaměstnavatelé, data, kontaktní údaje,
          cokoli jste napsali — je pryč v okamžiku dokončení nahrávání a nikdy se nikam neukládá
          ani nepřenáší.</p>

        <h2>5. Kdo vaše údaje zpracovává</h2>
        <p>Využíváme malý počet poskytovatelů služeb (zpracovatelů) výhradně k provozu služby.
          Vaše předplatné a e-mailová adresa jsou uloženy pouze v EU:</p>
        <ul>
          <li><b>Hetzner</b> (Německo) — hosting serveru a databáze. Zde žije vaše
            předplatné.</li>
          <li><b>Resend</b> (region EU) — odesílání našich e-mailů.</li>
          <li><b>Cloudflare</b> — DNS, HTTPS, ochrana proti botům (Turnstile) a souhrnná
            analytika návštěvnosti webu bez cookies.</li>
          <li><b>Google</b> (Google Ireland Ltd / Google LLC) — <i>pouze pokud</i> zvolíte
            „Pokračovat přes Google“. Google nám potvrdí vaši e-mailovou adresu, abychom vás
            mohli přihlásit, nebo — u nového uživatele — ověří vaši adresu, takže se můžete
            zaregistrovat bez samostatného potvrzovacího e-mailu. <b>Žádáme pouze o váš
            e-mail</b> a od Googlu <b>neukládáme nic navíc</b> — ani jméno, ani identifikátor
            účtu Google; pouze spárujeme potvrzenou adresu s předplatným. Pokud tuto možnost
            nepoužijete, Google nezpracovává nic.</li>
        </ul>
        <p>Výběr nabídek, které vám sedí, provádí krok párování s využitím AI, a ten běží mimo
          EU. Každý den — a znovu pokaždé, když na stránce svých shod stisknete
          „aktualizovat nabídky“ — mu posíláme ke každému odběrateli:</p>
        <ul>
          <li>náhodný identifikátor — <b>nikoli vaši e-mailovou adresu a nikoli vaše jméno</b>;</li>
          <li>vámi uvedené předvolby (pozice, dovednosti, zvolené země a města, formy práce,
            které přijmete, požadavky na vzdělání, které přijmete, a co jste studovali, pokud
            jste to uvedli, obory, které vás zajímají, typ práce);</li>
          <li>shrnutí odvozené z vašeho životopisu, pokud jste jej nahráli (oddíl 4);</li>
          <li>veřejné pracovní nabídky, které má seřadit.</li>
        </ul>
        <p>Tyto údaje posíláme <b>přímo z našeho serveru v EU společnosti Anthropic</b> (Claude),
          která je může zpracovávat ve Spojených státech. Přenos se opírá o standardní smluvní
          doložky Evropské komise a rámec EU–USA pro ochranu osobních údajů (Data Privacy
          Framework). Záměrně neobsahují <b>žádnou e-mailovou adresu ani jméno</b>, takže vás přímo
          neidentifikují. Jde o údaje <b>pseudonymizované, nikoli anonymní</b> — zůstávají osobními
          údaji a jako s takovými s nimi nakládáme, což je důvod, proč se přenos opírá o výše
          uvedené záruky; samotná vaše adresa nikdy neopustí naši databázi v EU. Výsledky se
          vracejí jako seznam odpovídajících nabídek k témuž náhodnému identifikátoru.</p>

        <h2>6. Cookies a statistiky používání</h2>
        <p>Web nenastavuje <b>žádné reklamní ani analytické cookies</b>. Nastavuje jednu
          <b> nezbytně nutnou cookie</b>, a to pouze pokud se přihlásíte: když otevřete odkaz,
          který jsme vám poslali e-mailem, nastavíme relační cookie, abyste na daném zařízení
          zůstali přihlášeni a nemuseli odkaz otevírat pokaždé znovu. Obsahuje náhodný token
          (žádný e-mail, žádné osobní údaje), <b>nepoužívá se ke sledování</b> a při odhlášení
          se smaže. Spolu s vaším předplatným ukládáme pouze jednosměrný otisk tohoto tokenu, a
          to nejvýše po <b>30 dnů</b> nečinnosti — kopie naší databáze tak nikdy neprozradí
          použitelné přihlášení. Cloudflare Turnstile (naše kontrola robotů bez CAPTCHA) může
          při registraci rovněž nastavit nezbytně nutný token k ověření, že jste člověk.</p>
        <p>Pro zlepšení webu zaznamenáváme malý počet událostí šetrných k soukromí — například
          „zobrazena úvodní stránka“, „zahájen registrační formulář“ nebo „nahrání životopisu
          selhalo“. Děje se tak <b>bez cookies</b>, a konkrétně:</p>
        <ul>
          <li><b>Nikdy neukládáme vaši IP adresu</b> — ani v surové podobě, ani jako otisk.
            Jediný údaj o poloze, který z vašeho připojení odvozujeme, je <b>země</b>. (Města ve
            vašich vyhledávacích předvolbách jsou něco jiného: ta si volíte sami — viz
            oddíl 2.)</li>
          <li>Ukládáme hrubou rodinu prohlížeče (např. „firefox“), nikdy celý otisk prohlížeče
            ani řetězec user-agent.</li>
          <li>Události se seskupují podle náhodného identifikátoru, který žije pouze ve vaší
            záložce prohlížeče a <b>maže se, jakmile ji zavřete</b>. Nedokáže propojit vaše
            návštěvy ani vás sledovat na jiném webu.</li>
          <li>Nikdy neobsahují obsah životopisu, e-mailovou adresu ani cokoli, co napíšete.</li>
          <li>Pokud váš prohlížeč posílá „Do Not Track“ nebo Global Privacy Control,
            nezaznamenáváme vůbec nic.</li>
        </ul>
        <p>Používáme také <b>Cloudflare Web Analytics</b> pro souhrnné počty zobrazení stránek.
          Je bez cookies už ze své podstaty a neshromažďuje žádné osobní údaje ani identifikátory
          napříč weby.</p>

        <h2>7. Jak dlouho údaje uchováváme</h2>
        <p>Údaje o vašem předplatném uchováváme po dobu, po kterou jste přihlášeni k odběru. Když
          se odhlásíte, zasílání se zastaví <b>okamžitě</b> a všechny aktivní přihlašovací relace
          skončí. Váš profil — předvolby, shrnutí životopisu, historie shod a technický záznam o
          tom, zda pro vás byl každý den přehled vytvořen (pouze počty, abychom poznali, kdy naše
          párování někoho tiše zklamalo) — je poté <b>do 30 dnů smazán</b>, a s ním i jeho
          přihlašovací relace. Na seznamu blokovaných adres ponecháváme trvale pouze vaši
          e-mailovou adresu: je to nezbytné minimum, které zaručí, že vám už nikdy nenapíšeme, a
          jeho smazání by tento účel zmařilo.</p>
        <p>Třicetidenní odstup je záměrný, nikoli odklad v náš prospěch. Někteří e-mailoví klienti
          a firemní bezpečnostní skenery automaticky následují odkazy, což může spustit odhlášení,
          na které jste nikdy neklikli. Toto okno znamená, že se to dá napravit e-mailem, místo
          aby to tiše zničilo vaše nastavení. Pokud chcete, abychom vše smazali okamžitě, napište
          nám a uděláme to (oddíl 8).</p>
        <p>Tyto události o používání mažeme po <b>180 dnech</b>. Před smazáním se
          redukují na anonymní denní součty — například „3. března zahájeno 42 registračních
          formulářů“ — které neobsahují žádné jednotlivé záznamy.</p>

        <h2>8. Vaše práva</h2>
        <p>Podle GDPR máte právo na přístup k údajům, jejich opravu, výmaz, omezení zpracování a
          přenositelnost, a dále právo vznést námitku proti zpracování nebo odvolat souhlas.
          Zabezpečený odkaz v každém e-mailu vám umožní upravit předvolby nebo se odhlásit bez
          hesla; pokud jej ztratíte, můžete si o jeho opětovné zaslání na vaši adresu požádat na
          stránce <b>Správa odběru</b>. S jakýmkoli jiným požadavkem se obraťte na
          <b> hello@jobdigest.eu</b> a my odpovíme bez zbytečného odkladu. Máte rovněž právo podat
          stížnost u dozorového úřadu (v České republice u Úřadu pro ochranu osobních údajů,
          uoou.gov.cz).</p>

        <h2>9. Zabezpečení</h2>
        <p>Veškerý provoz je šifrován přes HTTPS, odkazy pro správu používají neuhodnutelné tokeny
          namísto hesel a přístup k našim systémům je omezen. Neuchováváme žádná hesla, takže
          neexistuje databáze hesel, kterou by bylo možné odcizit. Zálohy databáze se šifrují
          dříve, než opustí server, takže kopie mimo server je bez klíče, který držíme odděleně,
          nepoužitelná. Žádný systém není dokonale bezpečný, ale přijímáme přiměřená opatření k
          ochraně vašich údajů.</p>

        <h2>10. Změny</h2>
        <p>Pokud tyto zásady podstatně změníme, aktualizujeme datum účinnosti výše a tam, kde je
          to vhodné, uvědomíme odběratele e-mailem.</p>

        <h2>11. Rozhodné znění</h2>
        <p>Tento text je překladem anglického znění. V případě rozporu mezi jazykovými verzemi je
          rozhodující <Link href={legalHref("en", "privacy")}>anglické znění</Link>.</p>

        <p style={{ marginTop: 24 }}>
          <Link href={localeHref("cs", "/")}>← Zpět na úvod</Link>
        </p>
      </div>
    </>
  );
}
