# Projekt GEO: dyskusja i odpowiedzi na pytania

## 1. Co właściwie robi GEO

GEO to nie „kolejna kryptowaluta” i nie bank.  
To **protokół ewidencji wzajemnych zobowiązań (długów)** między ludźmi i organizacjami.

Idea:

- każdy uczestnik może:
  - otwierać **linie zaufania** (limity kredytowe) wobec innych;
  - emitować **własne zobowiązania dłużne** (obietnice przyszłej dostawy towarów/usług);
- te zobowiązania są używane jako **środek wymiany** zamiast pieniędzy;
- system automatycznie:
  - znajduje **łańcuchy zaufania** między uczestnikami;
  - oblicza, jaki kredyt jest faktycznie dostępny wzdłuż tych łańcuchów;
  - wykonuje **clearing** (wzajemny kompensacyjny „zrzut” długów) w zamkniętych cyklach.

Nie ma żadnej wspólnej „monety GEO”, ani jednego blockchaina z historią wszystkich transakcji.

---

## 2. Podstawowe byty

### 2.1. Linia zaufania (trust line)

To limit kredytu, jaki jeden uczestnik przyznaje drugiemu.

Przykład:

- Alicja otwiera Bobowi linię zaufania 1000 jednostek w ekwiwalencie „hrywna”, „godzina pracy”, „kWh” — to drugorzędne.
- Bob może otrzymać od Alicji towary/usługi na kwotę 1000.
- W zamian u Alicji powstaje **zobowiązanie Boba**: Bob jest jej winien tyle samo w swoich towarach/usługach.

Ważne:

- bilans w GEO to faktycznie **pozycja netto**: ile inni są winni tobie minus ile jesteś winien ty innym;
- jeden uczestnik może mieć wiele linii zaufania:
  - do różnych osób,
  - w różnych ekwiwalentach.

### 2.2. Tranzytywność zaufania

Jeśli:

- Alicja ufa Bobowi do 1000,
- Bob ufa Davidowi do 500,

to David może wziąć u Alicji „na kredyt” do 500, **choć Alicja go nie zna**.

System widzi to jako łańcuch:

- Alicja kredytuje Boba;
- Bob kredytuje Davida;
- zobowiązania są „przekazywane” wzdłuż łańcucha.

W praktyce oznacza to: żeby zapłacić nieznanemu sklepikowi, wystarczy, że:

- jesteś połączony z nim przez kilka osób, które ufają sobie nawzajem;
- GEO znajdzie tę ścieżkę i policzy, ile może nią przejść.

GEO potrafi wykorzystywać łańcuchy o długości do ok. 6 węzłów.

---

## 3. Jak przebiega płatność (intucyjnie)

1. **Płatnik** wybiera odbiorcę i kwotę.
2. Protokół GEO:
   - szuka **ścieżek zaufania** od płatnika do odbiorcy (do 6 węzłów);
   - dla każdej ścieżki liczy maksymalny możliwy przepływ (min. pojemności na krawędziach);
   - zestawia z nich **zestaw ścieżek płatniczych**.
3. System rezerwuje zobowiązania na tych ścieżkach (żeby wykluczyć podwójne wykorzystanie limitu).
4. Wszyscy uczestnicy, których balanse się zmieniają, **lokalnie zatwierdzają** operację.
5. Jeśli choć jeden z nich się nie zgodzi lub nie odpowie — płatność jest odrzucana / cofana.
6. Jeśli wszyscy się zgadzają:
   - lokalne stany linii zaufania są aktualizowane,
   - w świecie fizycznym przekazywany jest towar/usługa.

Cechy:

- brak globalnego rejestru:
  - nie ma jednego „wąskiego gardła” jak centralny serwer czy jeden blockchain;
  - każda transakcja dotyczy jedynie niewielkiego podgrafu.
- atomowość:
  - **albo** wszyscy uczestnicy danego zestawu linii zmieniają stan,
  - **albo** nikt — brak stanów „pół po drodze”.

---

## 4. Automatyczny clearing (jak na rysunku)

Sytuacja:

- Osoba 1 jest winna Sklepowi 100;
- Sklep jest winien Osobie 2 — 100;
- Osoba 2 jest winna Osobie 1 — 100.

To zamknięta pętla długu. Ekonomicznie nikt nikomu nic nie jest winien — dług krąży po kole.

GEO:

- regularnie szuka cykli 3, 4, 5, 6 uczestników;
- gdy znajdzie cykl, inicjuje wirtualną „płatność na samego siebie” po tej pętli;
- w wyniku:
  - wszystkie zobowiązania w cyklu są zmniejszane o tę samą kwotę,
  - przy odpowiednich warunkach mogą być wyzerowane.

W opisie Dmy (oryginalny GEO Protocol):

- cykle 3–4‑węzłowe są wyszukiwane po każdej operacji;
- 5–6‑węzłowe — raz dziennie (ze względu na złożoność obliczeń).

Czyli system automatycznie robi to, co na poglądowej grafice jest pokazane jako seria kroków: długi przechodzą po sieci, a następnie „znikają” bez udziału pieniędzy.

---

## 5. Bezpieczeństwo, ryzyka i „anty‑oszustwo”

GEO **nie chroni** przed złym wyborem kontrahentów na poziomie protokołu — i jest to decyzja świadoma:

- linia zaufania = twój osobisty limit ryzyka;
- „bezpiecznik” przed oszustem leży nie w algorytmie, lecz w tym, **komu i na jaką kwotę otwierasz kredyt**.

Technicznie:

- ryzyko kredytowe jest **lokalne**:
  - ryzykujesz tylko w granicach limitów, które sam przyznajesz;
  - nikt nie może wydać więcej, niż łączny przepływ zaufania do niego w grafie pozwala.
- protokół liczy „przepływy”:

  - przykład z korespondencji: przepływ B → D może wynosić 2000, jeśli:
    - D ufa B na 1000,
    - D ufa A na 1000,
    - A ufa B na 1000;
  - zadaniem GEO jest policzyć ten maksymalny przepływ i pokazać go uczestnikowi.

- podwójne wydatkowanie:
  - jest wyeliminowane, bo każda transakcja:
    - rezerwuje środki na konkretnych liniach w fazie prepare,
    - wymaga lokalnego konsensusu wszystkich dotkniętych węzłów,
  - nie ma sensu „kopać bloków” ani przepełniać łańcucha — blockchaina w ogóle nie ma.

W oficjalnej specyfikacji akcentuje się **ACID‑podobne zachowanie** algorytmu płatności (atomowość, spójność, izolacja przy równoległych transakcjach).

---

## 6. Czym GEO *nie* jest

- GEO **nie jest pieniędzmi** w ścisłym sensie:
  - nie ma własnej jednostki wartości;
  - nie jest „bezpiecznym środkiem przechowywania” z odsetkami.

- GEO to **sieć kredytu barterowego**:
  - „walutą” są zobowiązania konkretnych osób/firm;
  - jednostkę rozliczeniową (ekwiwalent) wybierają strony:
    - fiat, towar, usługa, kWh, godzina pracy, koszyk itp.

---

## 7. Kluczowe różnice wobec klasycznego systemu

1. **Brak odsetek i prowizji protokołu**
   - Kredyt między uczestnikami jest bezodsetkowy i bezterminowy (moralnie należy go oczywiście spłacać pracą/towarami).
   - Transakcje w „czystym” protokole są bezpłatne, możliwe są więc mikropłatności.

2. **Decentralizacja**
   - Brak banku, centralnego serwera, wspólnej bazy transakcji, natywnej waluty.
   - Każdy:
     - emituje własne zobowiązania,
     - decyduje, komu ufa i na jaką kwotę.

3. **Konfiden­cjalność**
   - Publicznie widoczne są jedynie:
     - ID węzła,
     - sumaryczne limity wejściowe/wyjściowe,
     - zagregowane zobowiązania i bilans netto.
   - Treść konkretnych transakcji znają tylko dwie strony (i ich bezpośredni sąsiedzi na ścieżce, ale tylko w swojej części trasy, bez pełnego kontekstu).

4. **Elastyczny wybór ekwiwalentu**
   - Można odchodzić od inflacyjnych/niestabilnych walut fiat, korzystać z jednostek naturalnych lub mieszanych.

5. **Efekt społeczny**
   - Reputacja i wiarygodność uczestnika wpływa wprost na jego zdolność kredytową oraz „płynność” emitowanych przez niego zobowiązań.
   - GEO zamienia kapitał społeczny i zaufanie w zasób ekonomiczny.

---

## 8. Jak to się ma do „twojego rysunku”

Jeśli wyobrazisz sobie prosty schemat w kilku krokach:

1. Na początku uczestnicy i sklep są „pustymi” kółkami, bilans całej sieci = 0.
2. Ktoś bierze towar na kredyt, powstają strzałki długu, ale suma zobowiązań dodatnich i ujemnych nadal się bilansuje.
3. Pojawia się magazyn/dostawca, część zobowiązań jest przekazywana dalej w łańcuchu.
4. System GEO wykrywa, że długi utworzyły cykl typu:
   - ludzie → sklep → magazyn → ci sami ludzie,
5. Wykonuje clearing:
   - strzałki długu w cyklu się skracają lub znikają,
   - towary/usługi **zostały już** dostarczone, nic się nie „odwraca” w realnym świecie,
   - ilość formalnego długu w sieci spada.

To dokładnie to, co GEO robi „pod maską”:

- pozwala ludziom i firmom **wymieniać się na kredyt** po sieci zaufania,
- a potem **automatycznie „sprząta”** zbędne długi, szukając cykli i robiąc clearing.

---

Ten dokument jest bardziej „FAQ‑owy” и объяснительный, чем формальная спецификация. За строгой математикой и архитектурой лучше смотреть в:

- `docs/pl/concept/02-protokol-podstawowe-idee.md` (idee protokołu),
- `docs/pl/concept/07-geo-v0.1-podstawowy-protokol-sieci-kredytowej.md` (formalny opis GEO v0.1),
- `docs/pl/concept/03-istniejacy-protokol-geo-przeglad.md` (porównanie z oryginalnym GEO Protocol i innymi systemami).
