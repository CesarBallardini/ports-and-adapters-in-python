Feature: A teacher records grades and sees the standing that results
  UC-21 and UC-22, told through the browser. A teacher opens their section's grade sheet, records
  an attempt, and is shown what that attempt did to the student's standing in the subject.

  The rule this feature exists to demonstrate is that **the standing is the best attempt, not the
  last one**. Every attempt is kept; recording a 4 after a 7 changes nothing about whether the
  subject is passed, and a sheet that showed the 4 would be telling the teacher they had just
  failed a student they had not.

  It is also the first feature driven through an inbound adapter rather than through a use case
  directly, which is the other half of the claim: the same rules hold whichever door they come
  through, and none of them live in the adapter.

  Background:
    Given Tess teaches Mathematics
    And Sam is enrolled in that section
    And Sol is enrolled in that section
    And Tess is signed in

  Scenario: A first grade becomes the standing
    When Tess records 7 for Sam
    Then Sam's standing is 7
    And Sam has passed

  Scenario: A better attempt replaces the standing
    Given Tess has recorded 5 for Sam
    When Tess records 9 for Sam
    Then Sam's standing is 9
    And Sam has 2 attempts

  Scenario: A worse attempt does not replace the standing
    Given Tess has recorded 7 for Sam
    When Tess records 4 for Sam
    Then Sam's standing is 7
    And Sam has passed
    And Sam has 2 attempts

  Scenario: Grading one student leaves the other alone
    When Tess records 8 for Sam
    Then Sol has no grade

  Scenario: A grade outside the scale is refused and nothing is recorded
    When Tess records 11 for Sam
    Then the request is refused as invalid
    And Sam has no grade

  Scenario: A teacher of another section may not read this one
    Given Ivan teaches a different section
    And Ivan is signed in
    When Ivan opens the grade sheet
    Then the request is refused as forbidden

  Scenario: Nobody may record a grade without signing in
    Given the browser has no session
    When Tess records 7 for Sam
    Then the request is refused
    And nothing was recorded for Sam
