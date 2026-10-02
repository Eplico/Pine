# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

## Evergreen: Spaces, the Space switcher and the Archive

# Name shown for a Space the user has not named.
evergreen-default-space-name = Personal

evergreen-space-menu-button =
    .tooltiptext = Space options
evergreen-archive-button =
    .tooltiptext = Archived tabs
evergreen-new-space-button =
    .tooltiptext = New Space

# Variables:
#   $name (String) - The Space's name.
evergreen-space-dot =
    .tooltiptext = { $name }
    .aria-label = { $name }
# Variables:
#   $name (String) - The Space's name.
#   $shortcut (String) - The keyboard shortcut that switches to it, e.g. "Ctrl+Shift+1".
evergreen-space-dot-with-shortcut =
    .tooltiptext = { $name } ({ $shortcut })
    .aria-label = { $name }

evergreen-space-menu-edit =
    .label = Edit Space…
evergreen-space-menu-delete =
    .label = Delete Space…

## Space editor

evergreen-editor-title-new = New Space
evergreen-editor-title-edit = Edit Space
evergreen-editor-name =
    .placeholder = Space name
    .aria-label = Space name
evergreen-editor-colors =
    .aria-label = Space color
evergreen-editor-separate = Separate sign-ins
evergreen-editor-shared-hint = Shares cookies and sign-ins with your other shared Spaces.
evergreen-editor-separate-hint = Sites in this Space get their own cookies and sign-ins. History and bookmarks are still shared.
evergreen-editor-identity-shared = This Space shares cookies and sign-ins with your other shared Spaces.
evergreen-editor-identity-separate = This Space has its own cookies and sign-ins.
evergreen-editor-cancel = Cancel
evergreen-editor-create = Create Space
evergreen-editor-save = Save

evergreen-color-green =
    .aria-label = Green
evergreen-color-teal =
    .aria-label = Teal
evergreen-color-blue =
    .aria-label = Blue
evergreen-color-purple =
    .aria-label = Purple
evergreen-color-pink =
    .aria-label = Pink
evergreen-color-red =
    .aria-label = Red
evergreen-color-orange =
    .aria-label = Orange
evergreen-color-yellow =
    .aria-label = Yellow
evergreen-color-gray =
    .aria-label = Gray

## Deleting a Space

evergreen-delete-space-title = Delete Space
# Variables:
#   $name (String) - The Space's name.
#   $count (Number) - How many tabs the Space has in this window.
evergreen-delete-space-message =
    { $count ->
        [0] Delete “{ $name }”?
        [one] Delete “{ $name }”? Its tab will move to the Archive.
       *[other] Delete “{ $name }”? Its { $count } tabs will move to the Archive.
    }
# Variables:
#   $name (String) - The Space's name.
#   $count (Number) - How many tabs the Space has in this window.
evergreen-delete-space-message-separate =
    { $count ->
        [0] Delete “{ $name }”? Its cookies and sign-ins will be deleted.
        [one] Delete “{ $name }”? Its tab will move to the Archive, and its cookies and sign-ins will be deleted.
       *[other] Delete “{ $name }”? Its { $count } tabs will move to the Archive, and its cookies and sign-ins will be deleted.
    }

## Archive

evergreen-archive-title = Archive
# Variables:
#   $hours (Number) - Hours of inactivity before a tab is archived.
evergreen-archive-empty =
    { $hours ->
        [0] Automatic archiving is off. Tabs from deleted Spaces appear here.
       *[other] Tabs you haven’t used for { $hours } hours move here.
    }
evergreen-archive-clear = Clear Archive

## Tab context menu

evergreen-tab-move-to-space =
    .label = Move to Space
evergreen-tab-keep =
    .label = Keep in Space (never archive)
