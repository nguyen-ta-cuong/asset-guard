import SwiftUI

// A bare quote inside a multi-line string must not hide the references below it.
let displayNote = """
Designed for the 6.1" display
"""

struct ContentView: View {
    let page: Int

    var body: some View {
        VStack {
            Image(.appLogo)                       // Xcode 15+ generated symbol
            Image("hero_banner")                  // string literal
            Image("onboarding_\(page)")           // dynamic name -> keeps onboarding_*
            Image("Onboarding/welcome")           // "Provides Namespace" folder, by full name
            Image(.Onboarding.hero)               // ...and by its nested generated symbol
            // Image("unused_promo")              // comments do not count
        }
    }
}

enum Banner: String {
    case copy = "hero_banner_copy"                // indirect reference via enum
}
